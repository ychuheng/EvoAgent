"""M0b 样本台账检查：拒绝跨集合复用、重复正式使用与 holdout 来源。

按实施计划 §5.2/§5.5/§5.6 实现。本脚本只能发现 **ID、哈希、台账层面** 的显式复用；
它 **不能** 证明两个不同 fixture 在语义上独立——那需要预注册时的人工抽查，
结论记录在 `manifest.json` 的 `fixture_reviews` 中。

台账文件：

- `evals/datasets/manifest.json`：样本清单。每个样本记录稳定 ID、任务家族、fixture
  内容哈希、集合、`first_formal_run`、`retired_after_viewing`、预注册版本。
- `evals/datasets/run_ledger.jsonl`：**只追加**的正式使用记录。每次正式运行写一行，
  同一次配对实验的两臂写在同一行的两个 `arms` 里，视为同一次正式使用。
- `evals/datasets/skill_sources.json`：Skill 候选的提炼来源登记，供 `--register-source`
  追加、供 `check` 复查。

用法：
    python scripts/check_sample_ledger.py check
    python scripts/check_sample_ledger.py --register-source SKILL_ID=RUN_ID
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "evals" / "datasets"
FIXTURES = ROOT / "evals" / "fixtures"
MANIFEST = DATASETS / "manifest.json"
RUN_LEDGER = DATASETS / "run_ledger.jsonl"
SKILL_SOURCES = DATASETS / "skill_sources.json"

SCHEMA_VERSION = 1

# 正式样本集合：这些集合里的样本一旦被正式运行使用就不能再被别的实验复用。
HOLDOUT_SETS = frozenset({"m6-holdout", "m7-holdout"})
FORMAL_SETS = HOLDOUT_SETS | {"dev"}
ALL_SETS = FORMAL_SETS | {"legacy"}

# 正式使用记录里允许出现的 arm 名。配对实验固定两臂。
PAIRED_ARMS = ("control", "treatment")

_SKILL_ID = re.compile(r"^[a-z][a-z0-9_-]{1,127}$")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


class Audit:
    """收集问题；`check` 只在零问题时返回 0。"""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.summary: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def note(self, message: str) -> None:
        self.summary.append(message)


def fixture_hash(root: Path) -> str:
    """与 `build_m0b_holdout.fixture_hash` 同算法（排除 VCS 与字节码缓存）。"""

    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        parts = path.relative_to(root).parts
        if ".git" in parts or "__pycache__" in parts or path.suffix == ".pyc":
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def load_json(path: Path, audit: Audit) -> dict | None:
    if not path.is_file():
        audit.error(f"缺少文件：{path.relative_to(ROOT).as_posix()}")
        return None
    try:
        # utf-8-sig：Windows 编辑器常写 BOM，台账不该因此报一个看不懂的 JSON 错误。
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        audit.error(f"{path.name} 不是合法 JSON：{exc}")
        return None


def load_run_ledger(audit: Audit) -> list[dict]:
    """读取只追加台账。

    缺少文件不是错误：在第一次正式运行前台账就是空的。台账存在但某行损坏则报错，
    因为那意味着历史记录可能被改写过。
    """

    if not RUN_LEDGER.is_file():
        return []
    rows: list[dict] = []
    for number, line in enumerate(RUN_LEDGER.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            audit.error(f"run_ledger.jsonl 第 {number} 行不是合法 JSON：{exc}")
            continue
        if not isinstance(row, dict):
            audit.error(f"run_ledger.jsonl 第 {number} 行不是对象")
            continue
        row["_line"] = number
        rows.append(row)
    return rows


def check_manifest(manifest: dict, audit: Audit) -> dict[str, dict]:
    """结构、重复 ID、哈希与 fixture 落地检查。返回 sample_id -> 样本。"""

    if manifest.get("schema_version") != SCHEMA_VERSION:
        audit.error(f"manifest.schema_version 必须是 {SCHEMA_VERSION}")
    preregistration = manifest.get("preregistration")
    if not isinstance(preregistration, str) or not preregistration.strip():
        audit.error("manifest.preregistration 缺失")
    sets = manifest.get("sets")
    if not isinstance(sets, dict) or set(sets) != ALL_SETS:
        audit.error(f"manifest.sets 必须恰好覆盖 {sorted(ALL_SETS)}")

    reviews = manifest.get("fixture_reviews")
    if not isinstance(reviews, dict):
        audit.error("manifest.fixture_reviews 必须是对象")
        reviews = {}

    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        audit.error("manifest.samples 必须是非空数组")
        return {}

    by_id: dict[str, dict] = {}
    # fixture -> 它出现过的集合；用于「同内容跨集合复用」检查。
    fixture_sets: dict[str, set[str]] = {}
    # (集合, 哈希) -> 样本 ID；同一集合里两份不同 fixture 不允许同内容。
    set_hash_owner: dict[tuple[str, str], str] = {}

    for sample in samples:
        if not isinstance(sample, dict):
            audit.error("manifest.samples 含非对象条目")
            continue
        sample_id = sample.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            audit.error("样本缺少 sample_id")
            continue
        if sample_id in by_id:
            audit.error(f"sample_id 重复：{sample_id}")
            continue
        by_id[sample_id] = sample

        set_name = sample.get("set")
        if set_name not in ALL_SETS:
            audit.error(f"{sample_id}: set 非法（{set_name!r}）")
            continue
        if not isinstance(sample.get("task_family"), str) or not sample["task_family"]:
            audit.error(f"{sample_id}: 缺少 task_family")
        if set_name == "legacy":
            if sample.get("retired_after_viewing") is not True:
                audit.error(f"{sample_id}: legacy 样本必须 retired_after_viewing=true")
            if sample.get("preregistration") is not None:
                audit.error(f"{sample_id}: legacy 样本不得带 preregistration")
            if sample.get("fixture_hash") is not None:
                audit.warn(f"{sample_id}: legacy 样本带 fixture_hash，请确认它确实已曝光")
        else:
            if set_name in HOLDOUT_SETS:
                if sample.get("preregistration") != preregistration:
                    audit.error(f"{sample_id}: preregistration 与 manifest 不一致")
            elif sample.get("preregistration") not in (None, preregistration):
                audit.error(
                    f"{sample_id}: dev 样本的 preregistration 只能是 null 或 {preregistration}"
                )
            if "first_formal_run" not in sample:
                audit.error(f"{sample_id}: 缺少 first_formal_run 字段")
            if not isinstance(sample.get("retired_after_viewing"), bool):
                audit.error(f"{sample_id}: retired_after_viewing 必须是布尔值")

        fixture = sample.get("fixture")
        declared_hash = sample.get("fixture_hash")
        if fixture is None:
            if declared_hash is not None:
                audit.error(f"{sample_id}: 没有 fixture 却声明了 fixture_hash")
            continue
        if not isinstance(fixture, str) or not fixture:
            audit.error(f"{sample_id}: fixture 必须是非空相对路径")
            continue
        if not isinstance(declared_hash, str) or not declared_hash:
            audit.error(f"{sample_id}: 缺少 fixture_hash")
            continue

        root = (ROOT / fixture).resolve()
        fixture_root = FIXTURES.resolve()
        if not root.is_relative_to(fixture_root):
            audit.error(f"{sample_id}: fixture 必须位于 evals/fixtures 下（{fixture}）")
            continue
        if not root.is_dir():
            audit.error(f"{sample_id}: fixture 目录不存在（{fixture}）")
            continue
        actual = fixture_hash(root)
        if actual != declared_hash:
            audit.error(
                f"{sample_id}: fixture_hash 与磁盘不一致（声明 {declared_hash[:12]}…，"
                f"实际 {actual[:12]}…）"
            )
            continue

        # 集合归属按 fixture 目录名归并，抽查记录的键也是目录名。
        fixture_sets.setdefault(Path(fixture).name, set()).add(set_name)
        owner_key = (set_name, declared_hash)
        previous = set_hash_owner.get(owner_key)
        if previous is not None and previous != fixture:
            audit.error(f"{sample_id}: 同一集合 {set_name} 内 {fixture} 与 {previous} 内容哈希相同")
        set_hash_owner[owner_key] = fixture

    for fixture, sets in sorted(fixture_sets.items()):
        if "dev" in sets and sets & HOLDOUT_SETS:
            audit.error(f"{fixture}: 同一 fixture 同时用于 dev 与 holdout（{sorted(sets)}）")
        if len(sets & HOLDOUT_SETS) > 1:
            audit.error(f"{fixture}: 同一 fixture 被多个 holdout 集合共用（{sorted(sets)}）")

    for name, review in reviews.items():
        if not isinstance(review, dict):
            audit.error(f"fixture_reviews.{name} 必须是对象")
            continue
        for field in ("reviewer", "verdict", "checked_against", "note"):
            if not isinstance(review.get(field), str) or not review[field].strip():
                audit.error(f"fixture_reviews.{name}: 缺少 {field}")
        excluded = review.get("excluded")
        if not isinstance(excluded, list):
            audit.error(f"fixture_reviews.{name}: excluded 必须是数组")
            excluded = []
        if not excluded:
            audit.warn(
                f"fixture_reviews.{name}: 人工抽查没有记录任何排除项，"
                "请确认确实没有发现与 dev 相似的任务模板或解法"
            )
        if not review.get("reviewed_hash"):
            audit.error(f"fixture_reviews.{name}: 缺少 reviewed_hash")
        if name not in fixture_sets:
            audit.warn(f"fixture_reviews.{name}: 没有任何样本引用该 fixture")
        # 抽查记录冻结的是当时看到的 fixture 内容；内容变了，抽查结论就不再适用。
        declared = review.get("fixture_hash")
        if isinstance(declared, str) and declared:
            root = FIXTURES / name
            if not root.is_dir():
                audit.error(f"fixture_reviews.{name}: fixture 目录不存在")
            elif fixture_hash(root) != declared:
                audit.error(
                    f"fixture_reviews.{name}: 抽查时的 fixture_hash 与当前内容不一致，"
                    "必须重新抽查并更新预注册"
                )
        for item in excluded:
            audit.note(f"人工抽查排除项 {name}：{item}")

    return by_id


def check_run_ledger(
    rows: list[dict],
    by_id: dict[str, dict],
    audit: Audit,
) -> None:
    """正式使用记录检查。

    规则来自计划 §5.2/§5.5：每个候选只对封存 holdout 作一次正式运行；已看过的样本降为
    dev 并另取未暴露 holdout；同一次配对实验的两臂属于同一次正式使用。
    """

    seen_run_ids: dict[str, int] = {}
    seen_experiments: dict[str, int] = {}
    seen_candidates: dict[str, int] = {}
    # (sample_id, 实验) -> 已用过的 arm。
    arm_use: dict[tuple[str, str, str], str] = {}
    # 已曝光（看过结果后作废）的样本不得再当 holdout。
    retired = {
        sample_id for sample_id, sample in by_id.items() if sample.get("retired_after_viewing")
    }

    for row in rows:
        line = row["_line"]
        where = f"run_ledger.jsonl:{line}"
        run_id = row.get("run_id")
        experiment_id = row.get("experiment_id")
        candidate_id = row.get("candidate_id")
        milestone = row.get("milestone")

        if not isinstance(run_id, str) or not RUN_ID.match(run_id):
            audit.error(f"{where}: run_id 非法（{run_id!r}）")
            continue
        if run_id in seen_run_ids:
            audit.error(f"{where}: run_id 重复，台账只能追加（首次见第 {seen_run_ids[run_id]} 行）")
            continue
        seen_run_ids[run_id] = line

        if milestone not in {"M6", "M7"}:
            audit.error(f"{where}: milestone 必须是 M6 或 M7（{milestone!r}）")
        if not isinstance(experiment_id, str) or not experiment_id:
            audit.error(f"{where}: 缺少 experiment_id")
            continue
        if experiment_id in seen_experiments:
            audit.error(
                f"{where}: experiment_id 重复（首次见第 {seen_experiments[experiment_id]} 行）；"
                "一次正式使用只允许一行"
            )
            continue
        seen_experiments[experiment_id] = line

        if not isinstance(candidate_id, str) or not candidate_id:
            audit.error(f"{where}: 缺少 candidate_id")
            continue
        if candidate_id in seen_candidates:
            audit.error(
                f"{where}: 候选 {candidate_id} 已有正式运行"
                f"（第 {seen_candidates[candidate_id]} 行）；"
                "每个候选版本只能对封存 holdout 作一次正式运行"
            )
        else:
            seen_candidates[candidate_id] = line

        for field in ("candidate_revision", "model", "tools_revision", "dataset_sha256"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                audit.error(f"{where}: 缺少 {field}")

        arms = row.get("arms")
        if not isinstance(arms, dict) or not arms:
            audit.error(f"{where}: arms 必须是非空对象")
            continue

        expected_set = "m6-holdout" if milestone == "M6" else "m7-holdout"
        sample_sets: dict[str, set[str]] = {}
        for arm, sample_ids in sorted(arms.items()):
            if not isinstance(sample_ids, list) or not sample_ids:
                audit.error(f"{where}: arms.{arm} 必须是非空数组")
                continue
            if arm not in PAIRED_ARMS:
                audit.warn(f"{where}: arms.{arm} 不是标准配对臂名 {PAIRED_ARMS}")
            for sample_id in sample_ids:
                sample = by_id.get(sample_id)
                if sample is None:
                    audit.error(f"{where}: arms.{arm} 引用了台账中不存在的样本 {sample_id}")
                    continue
                set_name = sample.get("set")
                sample_sets.setdefault(sample_id, set()).add(set_name)
                if set_name == "legacy":
                    audit.error(f"{where}: {sample_id} 是历史已曝光样本，不得充当 holdout")
                elif set_name != expected_set:
                    audit.error(
                        f"{where}: {milestone} 必须使用 {expected_set}，"
                        f"但 {sample_id} 属于 {set_name}"
                    )
                if sample_id in retired:
                    audit.error(
                        f"{where}: {sample_id} 已 retired_after_viewing，不得再次充当 holdout"
                    )
                key = (sample_id, experiment_id, arm)
                if key in arm_use:
                    audit.error(f"{where}: {sample_id} 在同一实验里重复出现在 arms.{arm}")
                arm_use[key] = experiment_id
                first_formal_run = sample.get("first_formal_run")
                if first_formal_run is None:
                    sample["first_formal_run"] = run_id
                elif first_formal_run != run_id:
                    audit.error(
                        f"{where}: {sample_id} 的 first_formal_run 已是 {first_formal_run}"
                        f"（实验 {experiment_id}），不得跨实验复用到 {run_id}"
                    )

        for sample_id, sets in sorted(sample_sets.items()):
            if len(sets) > 1:
                audit.error(f"{where}: {sample_id} 同时出现在多个集合 {sorted(sets)}")

        if len(arms) > 1:
            for sample_id in sorted(sample_sets):
                used_arms = sorted(arm for arm, ids in arms.items() if sample_id in ids)
                if len(used_arms) > 1:
                    audit.error(
                        f"{where}: {sample_id} 同时出现在 {used_arms}；"
                        "配对实验的两臂必须使用互不相同的样本"
                    )

        if len(arms) == 2 and set(arms) != set(PAIRED_ARMS):
            audit.warn(f"{where}: 两臂实验的臂名不是 {PAIRED_ARMS}")
    audit.note(f"正式使用记录：{len(rows)} 行")


def check_skill_sources(sources: dict | None, rows: list[dict], audit: Audit) -> None:
    """Skill 提炼来源检查：holdout 的 Run 一律拒绝，已评分与仅看过轨迹都算。"""

    if sources is None:
        return
    if sources.get("schema_version") != SCHEMA_VERSION:
        audit.error(f"skill_sources.schema_version 必须是 {SCHEMA_VERSION}")
    entries = sources.get("sources")
    if not isinstance(entries, list):
        audit.error("skill_sources.sources 必须是数组")
        return

    rows_by_run: dict[str, list[dict]] = {}
    for row in rows:
        run_id = row.get("run_id")
        if isinstance(run_id, str):
            rows_by_run.setdefault(run_id, []).append(row)

    holdout_runs: dict[str, str] = {}
    for row in rows:
        run_id = row.get("run_id")
        if isinstance(run_id, str) and row.get("milestone") in {"M6", "M7"}:
            holdout_runs[run_id] = row.get("milestone")

    seen_skills: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"skill_sources.sources[{index}]"
        if not isinstance(entry, dict):
            audit.error(f"{where}: 必须是对象")
            continue
        skill_id = entry.get("skill_id")
        source_run_id = entry.get("source_run_id")
        if not isinstance(skill_id, str) or not _SKILL_ID.match(skill_id):
            audit.error(f"{where}: skill_id 非法（{skill_id!r}）")
        elif skill_id in seen_skills:
            audit.error(f"{where}: skill_id 重复：{skill_id}")
        else:
            seen_skills.add(skill_id)
        if not isinstance(source_run_id, str) or not source_run_id:
            audit.error(f"{where}: 缺少 source_run_id")
            continue
        if source_run_id in holdout_runs:
            audit.error(
                f"{where}: source_run_id {source_run_id} 属于 {holdout_runs[source_run_id]} "
                "holdout，禁止作为 Skill 提炼来源"
            )
        elif source_run_id not in rows_by_run:
            audit.error(
                f"{where}: source_run_id {source_run_id} 不在正式使用台账中，"
                "无法证明它不是 M6/M7 holdout"
            )
        if entry.get("traces_viewed") is True and source_run_id in holdout_runs:
            audit.error(f"{where}: 仅看过轨迹的 holdout Run 同样不得登记")
    audit.note(f"Skill 来源登记：{len(entries)} 条")


INDEPENDENCE_VERDICTS = frozenset({"independent_distinct", "independent_with_exclusions"})


def check_preregistration(reviews: dict, samples: dict[str, dict], audit: Audit) -> None:
    """预注册抽查结论必须能落到具体排除项上。

    只对 holdout 集合要求抽查：dev 是开发样本，本来就是被看过、可反复运行的一批，
    对 dev 提语义独立要求没有意义。
    """

    for sample_id, sample in sorted(samples.items()):
        if sample.get("set") not in HOLDOUT_SETS:
            continue
        fixture = sample.get("fixture")
        if not isinstance(fixture, str):
            continue
        # 抽查记录按 fixture 目录名索引，样本里记的是仓库相对路径。
        review = reviews.get(fixture) or reviews.get(Path(fixture).name)
        if not isinstance(review, dict):
            audit.error(f"{sample_id}: fixture {fixture} 没有人工抽查记录")
            continue
        verdict = review.get("verdict")
        if verdict not in INDEPENDENCE_VERDICTS:
            audit.error(f"{sample_id}: fixture {fixture} 的人工抽查结论不成立（{verdict!r}）")
        if verdict == "independent_with_exclusions" and not review.get("excluded"):
            audit.error(f"{sample_id}: fixture {fixture} 声明有排除项但 excluded 为空")


def run_check(*, source_skill: str | None, source_run: str | None) -> int:
    audit = Audit()
    manifest = load_json(MANIFEST, audit)
    sources = load_json(SKILL_SOURCES, audit) if SKILL_SOURCES.is_file() else None
    if manifest is None:
        report(audit)
        return 1

    rows = load_run_ledger(audit)
    by_id = check_manifest(manifest, audit)
    check_run_ledger(rows, by_id, audit)
    check_skill_sources(sources, rows, audit)
    reviews = manifest.get("fixture_reviews")
    check_preregistration(reviews if isinstance(reviews, dict) else {}, by_id, audit)

    if source_skill is not None:
        if not _SKILL_ID.match(source_skill):
            audit.error(f"--source-skill 非法：{source_skill!r}")
        elif source_run is None:
            audit.error("--source-skill 需要同时给出 --source-run")
        else:
            holdout = {row.get("run_id") for row in rows if row.get("milestone") in {"M6", "M7"}}
            if source_run not in {row.get("run_id") for row in rows}:
                audit.error(f"{source_run} 不在正式使用台账中，不能作为 Skill 提炼来源")
            elif source_run in holdout:
                audit.error(f"{source_run} 属于 M6/M7 holdout，不能作为 Skill 提炼来源")
            else:
                audit.note(f"来源预检通过：{source_skill} <- {source_run}")

    report(audit)
    return 1 if audit.errors else 0


def register_source(skill_id: str, source_run: str) -> int:
    """追加一条 Skill 来源登记；holdout 来源直接拒绝，不写文件。"""

    audit = Audit()
    manifest = load_json(MANIFEST, audit)
    if manifest is None:
        report(audit)
        return 1
    rows = load_run_ledger(audit)
    if audit.errors:
        report(audit)
        return 1

    if not _SKILL_ID.match(skill_id):
        audit.error(f"skill_id 非法：{skill_id!r}")
    holdout = {row.get("run_id") for row in rows if row.get("milestone") in {"M6", "M7"}}
    known = {row.get("run_id") for row in rows}
    if source_run not in known:
        audit.error(
            f"source_run_id {source_run} 不在正式使用台账中；无法证明它不是 M6/M7 holdout，拒绝登记"
        )
    elif source_run in holdout:
        audit.error(f"source_run_id {source_run} 属于 M6/M7 holdout，拒绝登记")
    if audit.errors:
        report(audit)
        return 1

    document = (
        json.loads(SKILL_SOURCES.read_text(encoding="utf-8-sig"))
        if SKILL_SOURCES.is_file()
        else {"schema_version": SCHEMA_VERSION, "sources": []}
    )
    document["sources"].append({"skill_id": skill_id, "source_run_id": source_run})
    SKILL_SOURCES.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    audit.note(f"已登记 {skill_id} <- {source_run}")
    report(audit)
    return 0


def report(audit: Audit) -> None:
    for message in audit.errors:
        print(f"[错误] {message}")
    for message in audit.warnings:
        print(f"[警告] {message}")
    for message in audit.summary:
        print(f"[信息] {message}")
    if audit.errors:
        print(f"台账检查失败：{len(audit.errors)} 个错误")
    else:
        print("台账检查通过")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--register-source",
        metavar="SKILL_ID",
        help="登记 Skill 提炼来源（必须同时给出 --source-run）",
    )
    parser.add_argument("--source-run", metavar="RUN_ID", help="Skill 提炼来源的 Run ID")
    parser.add_argument("--source-skill", metavar="SKILL_ID", help="只预检来源，不写文件")
    args = parser.parse_args()

    if args.register_source is not None:
        if args.source_run is None:
            parser.error("--register-source 需要 --source-run")
        return register_source(args.register_source, args.source_run)
    return run_check(source_skill=args.source_skill, source_run=args.source_run)


if __name__ == "__main__":
    raise SystemExit(main())
