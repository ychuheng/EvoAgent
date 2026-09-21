"""显式 opt-in 的 Linux Docker 验收；缺少环境时不伪装成实机通过。"""

import json
import os
import shutil
import sys
from uuid import uuid4

import pytest
from test_sandbox_controller import sandbox_env as sandbox_env

from evoagent.sandbox.docker import DockerDriver, SandboxError
from evoagent.sandbox.schema import SandboxSpec
from evoagent.sandbox.service import SandboxService
from evoagent.skills.canonical import content_hash

pytestmark = pytest.mark.docker


@pytest.fixture
async def real_sandbox(sandbox_env):
    if os.environ.get("EVOAGENT_RUN_DOCKER_TESTS") != "1":
        pytest.skip("set EVOAGENT_RUN_DOCKER_TESTS=1 in the Linux Docker acceptance environment")
    image = os.environ.get("EVOAGENT_TEST_SANDBOX_IMAGE")
    if sys.platform != "linux" or not shutil.which("docker") or not image:
        pytest.fail(
            "Docker acceptance requires Linux, Docker CLI and EVOAGENT_TEST_SANDBOX_IMAGE digest"
        )
    db, settings, _, request, _ = sandbox_env
    spec = SandboxSpec(image=image, cpus=0.25, timeout_seconds=5)
    settings.sandbox_profiles = {"python": spec}
    driver = DockerDriver(shutil.which("docker"))
    await driver.preflight(spec)
    service = SandboxService(settings, db.session_factory, driver)
    request = request.model_copy(
        update={"profile_hash": content_hash(spec.model_dump(mode="json"))}
    )
    try:
        yield service, driver, request, settings
    finally:
        await service.reap()


def script(request, code):
    return request.model_copy(
        update={"execution_id": uuid4(), "argv": ("/usr/local/bin/python", "-c", code)}
    )


async def test_real_cgroup_limits_uid_capabilities_and_throttling(real_sandbox):
    service, driver, request, _ = real_sandbox
    code = """
import os, json, time
from pathlib import Path
root=Path('/sys/fs/cgroup')
start=time.monotonic()
while time.monotonic()-start < 1.2: pass
print(json.dumps({'uid':os.getuid(), 'cpu':(root/'cpu.max').read_text().strip(),
'memory':(root/'memory.max').read_text().strip(),
'swap':(root/'memory.swap.max').read_text().strip(),
'pids':(root/'pids.max').read_text().strip(), 'stat':(root/'cpu.stat').read_text(),
'process':Path('/proc/self/status').read_text()}))
"""
    result = await service.run(script(request, code))
    report = json.loads(result["stdout"])
    assert report["uid"] == 10001
    quota, period = map(int, report["cpu"].split())
    assert quota / period == 0.25
    assert report["memory"] == "268435456" and report["swap"] == "0" and report["pids"] == "64"
    assert int(dict(line.split() for line in report["stat"].splitlines())["nr_throttled"]) > 0
    assert (
        "NoNewPrivs:\t1" in report["process"] and "CapEff:\t0000000000000000" in report["process"]
    )
    assert not await driver.managed()


@pytest.mark.parametrize("kind", ["memory", "pids", "disk", "output", "tree"])
async def test_real_resource_exhaustion_and_process_tree_cleanup(real_sandbox, kind):
    service, driver, request, _ = real_sandbox
    programs = {
        "memory": "x=bytearray(600*1024*1024)",
        "pids": "import os,time;\nwhile True:\n p=os.fork()\n if p==0: time.sleep(30); os._exit(0)",
        "disk": "open('/output/large','wb').write(b'x'*(32*1024*1024))",
        "output": "import os;\nwhile True: os.write(1,b'x'*65536)",
        "tree": (
            "import subprocess,time; subprocess.Popen(['/usr/local/bin/python','-c',"
            "'import time; time.sleep(60)']); time.sleep(60)"
        ),
    }
    try:
        result = await service.run(script(request, programs[kind]))
        assert result["return_code"] != 0
    except SandboxError:
        pass
    assert not await driver.managed()


async def test_real_network_none_cannot_bypass_proxy_or_reach_metadata(real_sandbox):
    service, driver, request, _ = real_sandbox
    code = """
import socket, json
result=[]
for family,address in [(socket.AF_INET,('1.1.1.1',80)),(socket.AF_INET,('169.254.169.254',80)),
                       (socket.AF_INET6,('2606:4700:4700::1111',80))]:
 s=socket.socket(family); s.settimeout(.4)
 try:
  s.connect(address); result.append('connected')
 except OSError: result.append('blocked')
 finally: s.close()
print(json.dumps(result))
"""
    result = await service.run(script(request, code))
    assert json.loads(result["stdout"]) == ["blocked"] * 3
    assert not await driver.managed()


@pytest.mark.parametrize(
    "code",
    [
        "import os; os.symlink('/etc/passwd','bad')",
        "import os; open('a','w').write('x'); os.link('a','b')",
        "import os; os.mkfifo('fifo')",
        "import os; os.mkdir('directory')",
    ],
)
async def test_real_artifact_escape_and_special_files_rejected(real_sandbox, code):
    service, driver, request, _ = real_sandbox
    with pytest.raises(SandboxError):
        await service.run(script(request, code))
    assert not await driver.managed()
