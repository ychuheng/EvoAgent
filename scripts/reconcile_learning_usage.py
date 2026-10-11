"""Trusted operator-only late usage settlement; not registered as an agent tool."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from evoagent.config import Settings
from evoagent.db.session import Database
from evoagent.learning.budget import LearningBudgetService, ManualUsageReview
from evoagent.learning.schema import LearningError


async def reconcile(args):
    review = ManualUsageReview.model_validate(json.loads(args.review.read_text(encoding="utf8")))
    settings = Settings(_env_file=args.env_file)
    async with Database(settings.database_url.get_secret_value()) as db:
        actual = await LearningBudgetService(db.session_factory, settings).review_unknown(
            args.reservation_id, review
        )
    print(json.dumps({"reservation_id": str(args.reservation_id), "actual_micros": actual}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--reservation-id", type=UUID, required=True)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(reconcile(args))
    except LearningError as error:
        parser.exit(1, error.code + "\n")
    except (OSError, ValueError):
        parser.exit(1, "invalid_usage_review_or_configuration\n")
    except Exception:
        parser.exit(1, "usage_review_database_unavailable\n")


if __name__ == "__main__":
    main()
