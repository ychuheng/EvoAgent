"""产物读取与导出（实施计划 §10 F-04）。

产物已经被 `ArtifactService` 登记过（含内容哈希与来源 Run），这里只做**只读出口**：

- `GET /artifacts/{id}`：元数据 + 文本预览（可在 `preview_bytes` 处截断）；
- `GET /artifacts/{id}/download`：按登记的名字与类型下载，响应头带 SHA-256，
  便于用户核对拿到的内容与记录一致。

两道限制是有意的：只读取数据库里已登记的产物（不接受任意路径），并按配置上限拒绝超大产物，
避免把"产物下载"变成绕过上下文预算读任意文件的通道。
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from evoagent.api.dependencies import DatabaseDependency, SettingsDependency
from evoagent.db.models import ArtifactRecord
from evoagent.privacy.artifact_access import (
    OUTCOME_CLEARED,
    ArtifactInjectionGuard,
    QuarantineReviewStale,
)
from evoagent.tools.base import ToolPermissionError
from evoagent.trace.artifacts import LocalArtifactStore

router = APIRouter(prefix="/artifacts", tags=["artifacts"])

MAX_PREVIEW_BYTES = 200_000

#: 服务端可信用户上下文里的操作者身份。单用户本机部署里人工操作统一记成 `human`
#: （与 `memory/service.py` 的决策路径一致）；它**不由请求体提供**。
TRUSTED_LOCAL_ACTOR = "human"

_PREVIEWABLE_SUFFIXES = (
    ".md",
    ".txt",
    ".json",
    ".csv",
    ".py",
    ".log",
    ".yaml",
    ".yml",
    ".html",
    ".xml",
)


def _filename(record: ArtifactRecord) -> str:
    return record.uri.rsplit("/", 1)[-1]


def _is_previewable(name: str, content_type: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(_PREVIEWABLE_SUFFIXES) or content_type.startswith("text/")


async def _load(record: ArtifactRecord, settings) -> bytes:
    if record.size_bytes > settings.artifact_download_max_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"产物 {record.size_bytes} 字节，超过下载上限 "
            f"{settings.artifact_download_max_bytes} 字节",
        )
    store = LocalArtifactStore(settings.artifact_root)
    try:
        content = await store.read(record.uri)
    except (OSError, ValueError) as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "产物内容不可读取：登记记录与存储不一致",
        ) from error
    actual = "sha256:" + hashlib.sha256(content).hexdigest()
    if actual != record.content_hash:
        # 内容与登记哈希不一致时不能把它当"可核对产物"发出去。
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "产物内容哈希与登记记录不一致，拒绝导出",
        )
    return content


@router.get("/{artifact_id}")
async def get_artifact(
    artifact_id: UUID,
    database: DatabaseDependency,
    settings: SettingsDependency,
    # 下限 256 字节：更小的切片会把多字节字符切一半，预览出现乱码。
    preview_bytes: int = Query(default=MAX_PREVIEW_BYTES, ge=256, le=MAX_PREVIEW_BYTES),
):
    async with database.session_factory() as session:
        record = await session.get(ArtifactRecord, artifact_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "产物不存在")

    name = _filename(record)
    content_type = str(record.attributes.get("content_type", "application/octet-stream"))
    body = await _load(record, settings)
    previewable = _is_previewable(name, content_type)
    preview: str | None = None
    truncated = False
    if previewable and preview_bytes > 0:
        sliced = body[:preview_bytes]
        truncated = len(body) > len(sliced)
        preview = sliced.decode("utf-8", errors="replace")
    return {
        "id": str(record.id),
        "run_id": str(record.run_id),
        "type": record.type,
        "name": name,
        "content_type": content_type,
        "content_hash": record.content_hash,
        "size_bytes": record.size_bytes,
        "created_at": record.created_at,
        "metadata": record.attributes,
        "preview": preview,
        "preview_truncated": truncated,
        "download_url": f"/api/v1/artifacts/{record.id}/download",
        "note": (
            "预览已截断，完整内容请下载；下载响应头带 SHA-256，可与 content_hash 核对。"
            if truncated
            else "内容为二进制或未启用预览时 preview 为空；下载响应头带 SHA-256 供核对。"
        ),
    }


class QuarantineReviewRequest(BaseModel):
    """产物详情页发起的单件复核请求。

    **刻意没有 actor 字段**：身份只能来自服务端可信用户上下文，不接受请求体伪造。
    模型工具与后台学习任务也没有到这里的路径。
    """

    reason: str = Field(min_length=1, max_length=2_000)
    expected_policy_version: int = Field(ge=0)
    expected_content_hash: str = Field(min_length=8, max_length=128)
    client_request_id: str = Field(min_length=1, max_length=128)


@router.post("/{artifact_id}/quarantine-review")
async def review_quarantine(
    artifact_id: UUID,
    body: QuarantineReviewRequest,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    """单件人工复核：按**当前**规则全量复查，通过才解除隔离（§2.4）。

    首版没有"忽略此秘密"的白名单：规则仍命中就继续隔离并返回 rejected，
    这不是接口失败，而是复核结论。
    """

    async with database.session_factory() as session:
        record = await session.get(ArtifactRecord, artifact_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "产物不存在")

    guard = ArtifactInjectionGuard(
        session_factory=database.session_factory,
        artifact_store=LocalArtifactStore(settings.artifact_root),
    )
    try:
        outcome = await guard.clear_quarantine(
            artifact_id=artifact_id,
            actor=TRUSTED_LOCAL_ACTOR,
            reason=body.reason,
            expected_policy_version=body.expected_policy_version,
            expected_content_hash=body.expected_content_hash,
            client_request_id=body.client_request_id,
        )
    except QuarantineReviewStale as error:
        # 过期条件：内容 hash、策略版本或隔离状态在复核期间变了。
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except ToolPermissionError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    return {
        "artifact_id": str(outcome.artifact_id),
        "outcome": outcome.outcome,
        "reason": outcome.reason,
        "rule_categories": list(outcome.categories),
        "policy_version": outcome.policy_version,
        "checked_hash": outcome.checked_hash,
        "replayed": outcome.replayed,
        "note": (
            "已解除隔离；历史 bytes 与 content_hash 未变，只更新了检查结论。"
            if outcome.outcome == OUTCOME_CLEARED
            else "仍保持隔离：当前规则依然命中该正文。纠正误报需要改规则并升策略版本，"
            "而不是绕过检测。"
        ),
    }


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: UUID,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> Response:
    async with database.session_factory() as session:
        record = await session.get(ArtifactRecord, artifact_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "产物不存在")
    body = await _load(record, settings)
    name = _filename(record)
    content_type = str(record.attributes.get("content_type", "application/octet-stream"))
    return Response(
        content=body,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-SHA256": record.content_hash,
        },
    )
