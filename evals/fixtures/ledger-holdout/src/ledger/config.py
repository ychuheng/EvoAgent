"""Runtime configuration for the ledger fixture."""

import os
from pathlib import Path

DEFAULT_LEDGER_FILE = Path("ledger.json")
RETRY_LIMIT = 2
DEFAULT_CURRENCY = "CNY"


def resolve_ledger_file() -> Path:
    """Return the ledger path, honouring the LEDGER_FILE override."""

    override = os.environ.get("LEDGER_FILE")
    if override:
        return Path(override)
    return DEFAULT_LEDGER_FILE
