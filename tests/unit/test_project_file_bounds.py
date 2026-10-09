"""Byte bounds must apply before line slicing and before destructive hashing."""

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from evoagent.projects.schema import ProjectAuthorization
from evoagent.tools.base import ToolExecutionError
from evoagent.tools.builtin.project_edit import DeleteFileArguments, DeleteFileTool
from evoagent.tools.builtin.project_file_read import ProjectFileReadArguments, ProjectFileReadTool


async def test_single_line_request_cannot_bypass_input_byte_limit(tmp_path, monkeypatch):
    (tmp_path / "large.txt").write_bytes(b"x" * 65)
    monkeypatch.setattr(Path, "read_bytes", lambda _: pytest.fail("unbounded read_bytes"))
    with pytest.raises(ToolExecutionError, match="读取上限"):
        await ProjectFileReadTool(tmp_path, max_file_bytes=64).invoke(
            ProjectFileReadArguments(path="large.txt", max_lines=1)
        )


@pytest.mark.parametrize("content", [b"", b"first\r\nsecond\r\n"])
async def test_original_hash_allows_bounded_read_then_checked_delete(tmp_path, content):
    path = tmp_path / "sample.txt"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    result = await ProjectFileReadTool(tmp_path).invoke(ProjectFileReadArguments(path=path.name))
    assert digest in result
    tool = DeleteFileTool(tmp_path, authorization=ProjectAuthorization.READ_WRITE)
    await tool.invoke(DeleteFileArguments(path=path.name, expected_sha256=digest, dry_run=True))
    assert path.exists()
    await tool.invoke(DeleteFileArguments(path=path.name, expected_sha256=digest))
    assert not path.exists()


@pytest.mark.parametrize("hash_value", [None, "0", "g" * 64])
def test_delete_requires_a_real_sha256_precondition(hash_value):
    body = {"path": "sample.txt"}
    if hash_value is not None:
        body["expected_sha256"] = hash_value
    with pytest.raises(ValidationError):
        DeleteFileArguments.model_validate(body)


async def test_delete_hashing_is_bounded_and_never_deletes_an_oversized_file(tmp_path, monkeypatch):
    path = tmp_path / "large.txt"
    path.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    monkeypatch.setattr(Path, "read_bytes", lambda _: pytest.fail("unbounded delete hashing"))
    with pytest.raises(ToolExecutionError, match="删除核对上限"):
        await DeleteFileTool(tmp_path, authorization=ProjectAuthorization.READ_WRITE).invoke(
            DeleteFileArguments(path=path.name, expected_sha256="0" * 64)
        )
    assert path.exists()
