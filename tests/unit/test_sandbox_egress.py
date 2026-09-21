import httpx
import pytest

from evoagent.sandbox.docker import DockerDriver
from evoagent.sandbox.egress import EgressTransport
from evoagent.sandbox.schema import SandboxSpec
from evoagent.tools.base import ToolPermissionError
from evoagent.tools.guards import URLGuard

IMAGE = "example/sandbox@sha256:" + "a" * 64


def test_fixed_spec_rejects_mutable_images_and_arbitrary_privileges():
    for changes in (
        {"image": "python:latest"},
        {"image": IMAGE, "privileged": True},
        {"image": IMAGE, "network": "host"},
        {"image": IMAGE, "memory_bytes": 2**40},
    ):
        with pytest.raises(ValueError):
            SandboxSpec(**changes)


def test_docker_argv_contains_enforced_resources_and_no_socket_or_host_network(tmp_path):
    spec = SandboxSpec(image=IMAGE)
    args = DockerDriver.create_args(
        "evoagent-sandbox-test", spec, tmp_path, ("/usr/local/bin/python", "-c", "print(1)")
    )
    assert "--network=none" in args and "--read-only" in args and "--cap-drop=ALL" in args
    assert "--security-opt=no-new-privileges=true" in args
    for flag, value in {
        "--user": "10001:10001",
        "--memory": "268435456",
        "--memory-swap": "268435456",
        "--pids-limit": "64",
        "--cpus": "1.0",
        "--log-driver=none": "",
    }.items():
        if value:
            assert args[args.index(flag) + 1] == value
    assert "--log-driver=none" in args
    assert any("nr_inodes=128" in item for item in args)
    assert not any("docker.sock" in item for item in args)
    assert any("dst=/input,readonly" in item for item in args)


@pytest.mark.parametrize(
    "addresses",
    [
        ("127.0.0.1",),
        ("169.254.169.254",),
        ("::1",),
        ("fd00::1",),
        ("fe80::1",),
        ("93.184.216.34", "10.0.0.1"),
        ("::ffff:127.0.0.1",),
    ],
)
async def test_public_egress_rejects_all_private_and_mixed_answers(addresses):
    async def resolver(host, port):
        return addresses

    contacted = []

    async def upstream(request):
        contacted.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(
        transport=EgressTransport(URLGuard(resolver), httpx.MockTransport(upstream))
    ) as client:
        with pytest.raises(ToolPermissionError):
            await client.get("https://example.com/")
    assert not contacted


@pytest.mark.parametrize("address", ["93.184.216.34", "2606:4700:4700::1111"])
async def test_actual_connection_is_pinned_and_preserves_host_and_tls(address):
    resolutions, requests = [], []

    async def resolver(host, port):
        resolutions.append(host)
        return (address,) if len(resolutions) == 1 else ("127.0.0.1",)

    async def upstream(request):
        requests.append(request)
        return httpx.Response(200, text="ok")

    transport = EgressTransport(URLGuard(resolver), httpx.MockTransport(upstream))
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await client.get("https://example.com:8443/data")).text == "ok"
        with pytest.raises(ToolPermissionError):
            await client.get("https://example.com:8443/changed")
    assert len(requests) == 1
    assert requests[0].url.host == address
    assert requests[0].headers["host"] == "example.com:8443"
    assert requests[0].extensions["sni_hostname"] == b"example.com"


async def test_redirect_hop_is_rechecked_without_forwarding_private_request():
    async def resolver(host, port):
        return ("93.184.216.34",)

    async def upstream(request):
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})

    async with httpx.AsyncClient(
        transport=EgressTransport(URLGuard(resolver), httpx.MockTransport(upstream)),
        follow_redirects=True,
    ) as client:
        with pytest.raises(ToolPermissionError):
            await client.get("https://example.com/")
