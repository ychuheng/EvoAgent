"""核对交付证据完整性；未知/待补项绝不自动视作 v0.4 完成。"""

import argparse
import hashlib
import json
from pathlib import Path

REQUIRED = frozenset(
    {
        "demos",
        "migrations",
        "backend",
        "frontend",
        "docker",
        "deployment",
        "phase3_regression",
        "real_skill",
        "real_runtime",
        "real_embedding",
    }
)


def verify(root, manifest):
    checks = manifest.get("checks", {})
    errors = [f"missing:{name}" for name in sorted(REQUIRED - checks.keys())]
    pending = []
    for name, check in checks.items():
        status = check.get("status")
        if status == "pending":
            pending.append(name)
            continue
        if status != "passed":
            errors.append(f"not_passed:{name}")
        if not check.get("artifacts"):
            errors.append(f"missing_artifacts:{name}")
        for artifact in check.get("artifacts", []):
            path = (root / artifact["path"]).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                errors.append(f"missing_artifact:{name}")
            elif (
                hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                != artifact["sha256"]
            ):
                errors.append(f"hash_mismatch:{name}")
    return errors, pending


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-pending", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "docs/reports/phase4-final-manifest.json").read_text("utf-8"))
    errors, pending = verify(root, manifest)
    print(
        json.dumps(
            {
                "evidence_errors": errors,
                "pending": pending,
                "release_ready": not errors and not pending,
            },
            ensure_ascii=False,
        )
    )
    return 1 if errors else 2 if pending and not args.allow_pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
