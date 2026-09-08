"""导入并可选冻结仓库内 JSON 评测数据集。"""

import argparse
import asyncio
import json
from pathlib import Path

from evoagent.config import Settings
from evoagent.db.session import Database
from evoagent.evals.datasets import EvalDatasetService, load_dataset_definition


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="导入 EvoAgent 评测数据集")
    result.add_argument("path", type=Path, help="相对于 EVOAGENT_EVAL_DATASET_ROOT 的 JSON 路径")
    result.add_argument("--freeze", action="store_true", help="导入成功后立即冻结该版本")
    return result


async def run(path: Path, *, freeze: bool) -> None:
    settings = Settings()
    definition = load_dataset_definition(path, root=settings.eval_dataset_root)
    async with Database(settings.database_url.get_secret_value()) as database:
        service = EvalDatasetService(database.session_factory)
        dataset = await service.import_definition(definition)
        if freeze and dataset.status.value == "draft":
            dataset = await service.freeze(dataset.id)
        print(
            json.dumps(
                {
                    "dataset_id": str(dataset.id),
                    "name": dataset.name,
                    "version": dataset.version,
                    "content_hash": dataset.content_hash,
                    "status": dataset.status.value,
                    "case_count": len(definition.cases),
                },
                ensure_ascii=False,
            )
        )


def main() -> None:
    arguments = parser().parse_args()
    asyncio.run(run(arguments.path, freeze=arguments.freeze))


if __name__ == "__main__":
    main()
