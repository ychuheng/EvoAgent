"""Skill 规范化序列化和内容哈希。"""

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def parse_definition(raw):
    from evoagent.skills.schema import SkillDefinition

    return (
        SkillDefinition.model_validate_json(raw)
        if isinstance(raw, str)
        else SkillDefinition.model_validate(raw)
    )


def serialize_definition(definition, schema_version=None):
    if schema_version is not None and schema_version != definition.schema_version:
        raise ValueError("definition schema conversion requires an explicit new version")
    return definition.model_dump(mode="json")
