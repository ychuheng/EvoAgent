"""Job-owned CI containers and official pull cache; never configure local hosts."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

MIRROR = "https://mirror.gcr.io"
POSTGRES = (
    "pgvector/pgvector@sha256:3e8b3adfd27b5707128f60956f62a793c3c9326ea8cfaf0eab7adccb5d700b21"
)


def cache_configuration(existing):
    if not isinstance(existing, dict):
        raise ValueError("Docker daemon configuration must be an object")
    mirrors = existing.get("registry-mirrors", [])
    if not isinstance(mirrors, list) or any(not isinstance(item, str) for item in mirrors):
        raise ValueError("Docker mirrors must be a string list")
    return {**existing, "registry-mirrors": list(dict.fromkeys([MIRROR, *mirrors]))}


def runner_owner():
    if (
        sys.platform != "linux"
        or os.getenv("GITHUB_ACTIONS") != "true"
        or os.getenv("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.getenv("DOCKER_HOST")
    ):
        raise RuntimeError("CI Docker operations require an isolated GitHub-hosted Linux runner")
    parts = [
        os.getenv(key)
        for key in (
            "GITHUB_REPOSITORY",
            "GITHUB_RUN_ID",
            "GITHUB_RUN_ATTEMPT",
            "GITHUB_JOB",
        )
    ]
    if any(not item for item in parts):
        raise RuntimeError("CI ownership metadata is missing")
    return ":".join(parts)


def docker(*args):
    return subprocess.run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    ).stdout.strip()


def configure():
    runner_owner()
    path = Path("/etc/docker/daemon.json")
    existing = json.loads(path.read_text()) if path.exists() else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache_configuration(existing)) + "\n")
    subprocess.run(["systemctl", "restart", "docker"], check=True, timeout=60)
    print("Configured official Docker Hub cache on this isolated CI runner")


def start(name, image, port, *extra):
    owner = runner_owner()
    identifier = docker(
        "run",
        "--detach",
        "--name",
        "evoagent-ci-" + name,
        "--label",
        "evoagent.ci.owner=" + owner,
        "--publish",
        "127.0.0.1:" + port,
        *extra,
        image,
    )
    print("Started job-owned", name, identifier)
    return identifier


def start_tests():
    start("redis", "redis:7.4-alpine", "6379:6379")
    postgres = start(
        "postgres",
        POSTGRES,
        "5432:5432",
        "--env",
        "POSTGRES_DB=evoagent_test",
        "--env",
        "POSTGRES_USER=evoagent",
        "--env",
        "POSTGRES_PASSWORD=evoagent",
    )
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        ready = subprocess.run(
            [
                "docker",
                "exec",
                postgres,
                "pg_isready",
                "-q",
                "-U",
                "evoagent",
                "-d",
                "evoagent_test",
            ],
            capture_output=True,
            timeout=10,
        )
        if ready.returncode == 0:
            if docker("exec", "evoagent-ci-redis", "redis-cli", "ping") != "PONG":
                raise RuntimeError("job-owned Redis did not become ready")
            print("Job-owned PostgreSQL is ready; pinned image", POSTGRES)
            return
        time.sleep(1)
    raise RuntimeError("job-owned PostgreSQL did not become ready")


def stop():
    owner = runner_owner()
    identities = docker(
        "ps", "--all", "--quiet", "--filter", "label=evoagent.ci.owner=" + owner
    ).splitlines()
    for identity in identities:
        metadata = json.loads(docker("inspect", identity))[0]
        if metadata["Config"]["Labels"].get("evoagent.ci.owner") != owner or not metadata[
            "Name"
        ].startswith("/evoagent-ci-"):
            raise RuntimeError("refusing to clean a container outside this CI job")
        docker("rm", "--force", "--volumes", identity)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("configure", "test-services", "registry", "stop"))
    operation = parser.parse_args().operation
    runner_owner()
    if operation == "configure":
        configure()
    elif operation == "test-services":
        start_tests()
    elif operation == "registry":
        start("registry", "registry:2", "5000:5000")
    else:
        stop()


if __name__ == "__main__":
    main()
