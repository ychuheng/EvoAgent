"""Runtime configuration for the beacon fixture."""

import os
from pathlib import Path

DEFAULT_ENCODING = "utf-8"
FALLBACK_ENCODINGS = ("gb18030", "cp1252")
RETRY_LIMIT = 3
DEFAULT_REPORT = Path("report.txt")


def resolve_data_root() -> Path:
    """Return the data root, honouring the BEACON_DATA_ROOT override."""

    override = os.environ.get("BEACON_DATA_ROOT")
    if override:
        return Path(override)
    return Path("data")
