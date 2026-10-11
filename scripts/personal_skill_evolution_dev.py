"""Explicit offline dev scenarios through real services; never model efficacy."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCENARIOS = {
    "reuse-revision-brake": "tests/integration/test_personal_evolution_brake_e2e.py::"
    "test_actual_personal_use_feedback_and_three_failures_stop_next_adoption",
    "repeat-failure-aggregation": "tests/integration/test_revision_aggregation.py::"
    "test_three_frozen_sources_generate_one_draft_and_retain_all_revocation_edges",
}


def read_fixture(path):
    if path.stat().st_size > 8192:
        raise ValueError("dev fixture exceeds 8192 bytes")
    fixture = json.loads(path.read_text(encoding="utf8"))
    if (
        not isinstance(fixture, dict)
        or set(fixture) != {"schema_version", "data_role", "scenarios"}
        or type(fixture["schema_version"]) is not int
        or fixture["schema_version"] != 1
        or fixture["data_role"] != "dev"
        or not isinstance(fixture["scenarios"], list)
        or not 1 <= len(fixture["scenarios"]) <= len(SCENARIOS)
        or any(not isinstance(s, str) or s not in SCENARIOS for s in fixture["scenarios"])
        or len(set(fixture["scenarios"])) != len(fixture["scenarios"])
    ):
        raise ValueError("explicit dev fixture with known unique scenarios required")
    return fixture


def emit_evidence(name, payload):
    """Opt-in test metadata export; no output in ordinary product/test execution."""
    directory = os.environ.get("EVOAGENT_DEV_EVIDENCE_DIRECTORY")
    if directory is None:
        return
    if name not in SCENARIOS:
        raise ValueError("unknown dev evidence scenario")
    (Path(directory) / (name + ".json")).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf8"
    )


def source_digest(root):
    paths = (
        subprocess.check_output(
            ["git", "ls-files", "-z", "--", "src", "scripts", "tests"], cwd=root
        )
        .decode()
        .split("\0")
    )
    digest = hashlib.sha256()
    for name in sorted(set(paths) | {"scripts/personal_skill_evolution_dev.py"}):
        if name and (root / name).is_file():
            digest.update(name.encode() + b"\0")
            digest.update(hashlib.sha256((root / name).read_bytes()).digest())
    return digest.hexdigest()


def execute(fixture_path, output):
    fixture = read_fixture(fixture_path)
    root = Path(__file__).resolve().parents[1]
    before = source_digest(root)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    fixture_hash = hashlib.sha256(
        json.dumps(fixture, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    # Fixtures create their own SQLite state. Never inherit product/test DB URLs,
    # provider credentials, custom pytest options or private application settings.
    environment = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("EVOAGENT_")
        and k not in {"DATABASE_URL", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "PYTEST_ADDOPTS"}
    }
    with tempfile.TemporaryDirectory(prefix="evoagent-dev-evolution-") as directory:
        environment["EVOAGENT_DEV_EVIDENCE_DIRECTORY"] = directory
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-o",
                    "addopts=",
                    "--",
                    *(SCENARIOS[name] for name in fixture["scenarios"]),
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf8",
                timeout=900,
            )
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            exit_code = 124
        evidence = {
            name: json.loads((Path(directory) / (name + ".json")).read_text(encoding="utf8"))
            for name in fixture["scenarios"]
            if (Path(directory) / (name + ".json")).is_file()
        }
    after = source_digest(root)
    report = {
        "schema_version": 1,
        "data_role": "dev",
        "provider": "injected_offline",
        "paid_calls": 0,
        "real_model_efficacy_verified": False,
        "human_judgments": "synthetic fixtures, not actual human review",
        "fixture_canonical_sha256": fixture_hash,
        "git_sha": revision,
        "end_git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "source_digest_before": before,
        "source_digest_after": after,
        "scenarios": fixture["scenarios"],
        "evidence": evidence,
        "test_exit_code": exit_code,
        "complete": exit_code == 0
        and before == after
        and set(evidence) == set(fixture["scenarios"]),
        "limitations": [
            "New-input semantics and adoption wiring only; no statistical benefit.",
            "Test-owned temporary SQLite; no product server or user files.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    return 0 if report["complete"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return execute(args.fixture, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
