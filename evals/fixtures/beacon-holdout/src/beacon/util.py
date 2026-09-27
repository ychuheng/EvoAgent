"""Small text helpers for the beacon fixture."""

import re


def normalize_name(name: str) -> str:
    """已知缺陷：只做小写，不裁掉前后空格。"""

    return name.lower()


def is_blank(text: str) -> bool:
    return not text.strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize_name(text)).strip("-")
