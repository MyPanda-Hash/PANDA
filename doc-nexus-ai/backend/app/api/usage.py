from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import PurePath
from typing import Any, Literal
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

from app.api.deps import current_user, usage_viewer
from app.core.database import transaction
from app.core.security import iso, new_token, now
from app.models.artifacts import Artifact
from app.models.auth import User
from app.models.writing import WritingProject
from app.repositories import audit, usage, writing
from app.schemas.usage import (
    AdminUsageSummaryResponse,
    ArtifactListResponse,
    ArtifactPreviewResponse,
    ArtifactRenameRequest,
    ArtifactResponse,
    ArtifactShareRequest,
    ModelUsageSummaryResponse,
    OperationDetailResponse,
    OperationListResponse,
    OperationResponse,
    UsageEventResponse,
    UsageSnapshotResponse,
    UserUsageSummaryResponse,
)
from app.services import artifact_preview
from app.services.chat_knowledge import require_reader_spaces

router = APIRouter(tags=["usage"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="资源不存在")


def _operation_response(item: usage.OperationReadModel) -> OperationResponse:
    return OperationResponse(id=item.id, feature=item.feature, action=item.action, status=item.status, model=str(item.usage.get("model") or item.usage["provider"]), usage=UsageSnapshotResponse(**item.usage), resource_path=item.resource_path, created_at=item.created_at, completed_at=item.completed_at)


def _artifact_response(artifact: Artifact, source_title: str | None = None) -> ArtifactResponse:
    return ArtifactResponse(id=artifact.id, name=artifact.name, artifact_type=artifact.artifact_type, status=artifact.status, resource_path=f"/writing/{artifact.project_id}", content_type=artifact.content_type, content_size=artifact.content_size, checksum_sha256=artifact.checksum_sha256, source_version_ids=usage.artifact_source_version_ids(artifact), manifest=usage.artifact_manifest(artifact), created_at=artifact.created_at, updated_at=artifact.updated_at,
        deleted_at=artifact.deleted_at, source_title=source_title,
        share_path=f"/artifacts?share={artifact.share_id}" if artifact.share_id and artifact.share_checksum == artifact.checksum_sha256 else None,
        share_scope=artifact.share_scope)


@router.get("/usage/operations", response_model=OperationListResponse)
def list_operations(feature: str | None = None, model: str | None = None, status: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None, offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100), user: User = Depends(current_user)) -> OperationListResponse:
    with transaction() as db:
        items, next_offset = usage.list_operations(db, owner_user_id=user.id, feature=feature, model=model, status=status, date_from=date_from, date_to=date_to, offset=offset, limit=limit)
        return OperationListResponse(items=[_operation_response(item) for item in items], next_offset=next_offset)


@router.get("/admin/usage/summary", response_model=AdminUsageSummaryResponse)
def admin_usage_summary(
    feature: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    include_zero: bool = Query(default=True),
    _: User = Depends(usage_viewer),
) -> AdminUsageSummaryResponse:
    with transaction() as db:
        result = usage.summarize_usage_by_user(
            db,
            feature=feature,
            date_from=date_from,
            date_to=date_to,
            include_zero=include_zero,
        )
        return AdminUsageSummaryResponse(
            items=[UserUsageSummaryResponse(**item.__dict__) for item in result.items],
            total_users=result.total_users,
            users_with_usage=result.users_with_usage,
            total_operations=result.total_operations,
            total_input_tokens=result.total_input_tokens,
            total_input_uncached_tokens=result.total_input_uncached_tokens,
            total_cache_read_tokens=result.total_cache_read_tokens,
            total_cache_write_tokens=result.total_cache_write_tokens,
            total_output_tokens=result.total_output_tokens,
            total_tokens=result.total_tokens,
            total_cost=result.total_cost,
            by_model=[ModelUsageSummaryResponse(**m.__dict__) for m in result.by_model],
        )


@router.get("/admin/schedules/overview")
def admin_schedules_overview(
    _: User = Depends(usage_viewer),
) -> dict[str, Any]:
    """定时任务运行健康总览：状态分布、队列深度与积压信号。"""
    from app.models.schedules import Schedule, ScheduleRun

    with transaction() as db:
        stamp = now()
        schedule_counts = {
            str(status): int(count)
            for status, count in db.execute(
                select(Schedule.status, func.count()).group_by(Schedule.status)
            )
        }
        active_run_counts = {
            str(status): int(count)
            for status, count in db.execute(
                select(ScheduleRun.status, func.count())
                .where(
                    ScheduleRun.status.in_(
                        ("queued", "running", "awaiting_user", "outcome_unknown")
                    )
                )
                .group_by(ScheduleRun.status)
            )
        }
        oldest_queued = db.scalar(
            select(func.min(ScheduleRun.next_attempt_at)).where(
                ScheduleRun.status == "queued"
            )
        )
        overdue_enabled = int(
            db.scalar(
                select(func.count())
                .select_from(Schedule)
                .where(
                    Schedule.status == "enabled",
                    Schedule.next_run_at.is_not(None),
                    Schedule.next_run_at < stamp,
                )
            )
            or 0
        )
        blocked_reasons = [
            {"reason": str(reason or "unknown"), "count": int(count)}
            for reason, count in db.execute(
                select(Schedule.blocked_reason, func.count())
                .where(Schedule.status == "blocked")
                .group_by(Schedule.blocked_reason)
                .order_by(desc(func.count()))
                .limit(8)
            )
        ]
        return {
            "schedules": schedule_counts,
            "active_runs": active_run_counts,
            "queue": {
                "oldest_queued_at": iso(oldest_queued) if oldest_queued else None,
                "overdue_enabled": overdue_enabled,
            },
            "blocked_reasons": blocked_reasons,
            "generated_at": iso(stamp),
        }


@router.get("/usage/operations/{operation_id}", response_model=OperationDetailResponse)
def get_operation(operation_id: str, user: User = Depends(current_user)) -> OperationDetailResponse:
    with transaction() as db:
        operation = usage.owned_operation(db, operation_id, user.id)
        if not operation:
            raise _not_found()
        response = _operation_response(operation)
        return OperationDetailResponse(**response.model_dump(), kind=f"{operation.feature}:{operation.action}", label=f"{operation.feature}：{operation.action}", operation_id=operation.id, usage_events=[UsageEventResponse(**usage.operation_usage_event(operation))])


@router.get("/artifacts", response_model=ArtifactListResponse)
def list_artifacts(view: Literal["active", "trash"] = "active", user: User = Depends(current_user)) -> ArtifactListResponse:
    with transaction() as db:
        items = usage.list_owned_artifacts(db, user.id, trash=view == "trash")
        titles = dict(db.execute(select(WritingProject.id, WritingProject.title).where(WritingProject.id.in_({item.project_id for item in items}))).all()) if items else {}
        return ArtifactListResponse(items=[_artifact_response(item, titles.get(item.project_id)) for item in items])


@router.patch("/artifacts/{artifact_id}", response_model=ArtifactResponse)
def rename_artifact(artifact_id: str, payload: ArtifactRenameRequest, user: User = Depends(current_user)) -> ArtifactResponse:
    with transaction() as db:
        artifact = usage.owned_artifact(db, artifact_id, user.id)
        if artifact is None:
            raise _not_found()
        name = payload.name.strip()
        suffix = PurePath(artifact.name).suffix
        if not name or any(char in name for char in '/\\') or any(ord(char) < 32 for char in name):
            raise HTTPException(status_code=422, detail="请使用有效文件名称，不包含路径或控制字符")
        if suffix and not name.lower().endswith(suffix.lower()):
            name += suffix
        if len(name) > 255:
            raise HTTPException(status_code=422, detail="文件名称不能超过 255 个字符")
        artifact.name = name
        artifact.updated_at = now()
        audit.record(db, user.id, "artifact_renamed", artifact.id, "success")
        return _artifact_response(artifact)


@router.delete("/artifacts/{artifact_id}", status_code=204)
def trash_artifact(artifact_id: str, user: User = Depends(current_user)) -> None:
    with transaction() as db:
        artifact = usage.owned_artifact(db, artifact_id, user.id, include_deleted=True)
        if artifact is None:
            raise _not_found()
        if artifact.status == "regenerating":
            raise HTTPException(status_code=409, detail="成果正在生成，请完成后再移入回收站")
        artifact.deleted_at = artifact.deleted_at or now()
        artifact.share_id = None
        artifact.share_scope = None
        artifact.share_checksum = None
        artifact.share_space_ids_json = None
        audit.record(db, user.id, "artifact_trashed", artifact.id, "success")


@router.post("/artifacts/{artifact_id}/restore", response_model=ArtifactResponse)
def restore_artifact(artifact_id: str, user: User = Depends(current_user)) -> ArtifactResponse:
    with transaction() as db:
        artifact = usage.owned_artifact(db, artifact_id, user.id, include_deleted=True)
        if artifact is None:
            raise _not_found()
        artifact.deleted_at = None
        artifact.updated_at = now()
        audit.record(db, user.id, "artifact_restored", artifact.id, "success")
        return _artifact_response(artifact)


@router.delete("/artifacts/{artifact_id}/permanent", status_code=204)
def purge_artifact(artifact_id: str, user: User = Depends(current_user)) -> None:
    with transaction() as db:
        artifact = usage.owned_artifact(db, artifact_id, user.id, include_deleted=True)
        if artifact is None:
            raise _not_found()
        if artifact.deleted_at is None:
            raise HTTPException(status_code=409, detail="请先将成果移入回收站")
        audit.record(db, user.id, "artifact_deleted", artifact.id, "success")
        db.delete(artifact)


@router.post("/artifacts/{artifact_id}/share", response_model=ArtifactResponse)
def share_artifact(artifact_id: str, payload: ArtifactShareRequest, user: User = Depends(current_user)) -> ArtifactResponse:
    with transaction() as db:
        artifact = usage.owned_artifact(db, artifact_id, user.id)
        if artifact is None:
            raise _not_found()
        if artifact.status != "ready" or not usage.has_downloadable_snapshot(artifact):
            raise HTTPException(status_code=409, detail="成果当前不可分享，请重新生成")
        project = db.get(WritingProject, artifact.project_id)
        if project is None:
            raise _not_found()
        spaces = require_reader_spaces(db, user, writing.load_space_ids(project.selected_space_ids))
        if payload.share_scope == "space" and not spaces:
            raise HTTPException(status_code=422, detail="按空间分享需要来源文稿引用资料空间")
        artifact.share_id = new_token(24)
        artifact.share_scope = payload.share_scope
        artifact.share_space_ids_json = json.dumps(spaces)
        artifact.share_checksum = artifact.checksum_sha256
        audit.record(db, user.id, "artifact_shared", artifact.id, "success")
        return _artifact_response(artifact)


@router.delete("/artifacts/{artifact_id}/share", status_code=204)
def revoke_artifact_share(artifact_id: str, user: User = Depends(current_user)) -> None:
    with transaction() as db:
        artifact = usage.owned_artifact(db, artifact_id, user.id)
        if artifact is None:
            raise _not_found()
        artifact.share_id = None
        artifact.share_scope = None
        artifact.share_checksum = None
        artifact.share_space_ids_json = None
        audit.record(db, user.id, "artifact_share_revoked", artifact.id, "success")


@router.get("/artifacts/shared/{share_id}")
def get_shared_artifact(share_id: str, user: User = Depends(current_user)) -> dict:
    with transaction() as db:
        artifact = usage.shared_artifact(db, share_id, user)
        if artifact is None:
            raise _not_found()
        return {"id": artifact.id, "name": artifact.name, "content_size": artifact.content_size,
                "href": f"/api/files/artifact-shares/{share_id}/{quote(artifact.name, safe='')}"}


class ArtifactDownloadSelection(BaseModel):
    artifact_ids: list[str] = Field(min_length=1, max_length=50)


@router.post("/artifacts:download", response_class=Response)
def download_artifacts(payload: ArtifactDownloadSelection, user: User = Depends(current_user)) -> Response:
    with transaction() as db:
        artifacts = []
        total = 0
        for artifact_id in dict.fromkeys(payload.artifact_ids):
            artifact = usage.owned_artifact(db, artifact_id, user.id)
            if artifact is None:
                raise _not_found()
            total += artifact.content_size or 0
            if total > 100 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="每次批量下载最多 100 MB，请减少所选文件")
            if artifact.status != "ready" or not usage.has_downloadable_snapshot(artifact):
                raise HTTPException(status_code=409, detail="所选成果包含不可下载的文件，请刷新后重试")
            artifacts.append(artifact)
        buffer = io.BytesIO()
        with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
            for index, artifact in enumerate(artifacts, 1):
                safe_name = artifact.name.replace("\\", "_").replace("/", "_")
                archive.writestr(f"{index:02d}-{safe_name}", artifact.content)
        return Response(buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": "attachment; filename=artifacts.zip"})


@router.get("/artifacts/{artifact_id}", response_model=ArtifactResponse)
def get_artifact(artifact_id: str, user: User = Depends(current_user)) -> ArtifactResponse:
    with transaction() as db:
        artifact = usage.owned_artifact(db, artifact_id, user.id)
        if not artifact:
            raise _not_found()
        return _artifact_response(artifact)


@router.post("/artifacts/{artifact_id}/download", response_class=Response)
def request_download(artifact_id: str, user: User = Depends(current_user)) -> Response:
    with transaction() as db:
        artifact = usage.owned_artifact(db, artifact_id, user.id)
        if not artifact:
            raise _not_found()
        if artifact.status != "ready" or not usage.has_downloadable_snapshot(artifact):
            raise HTTPException(status_code=409, detail="成果当前不可下载，请重新生成")
        assert artifact.content is not None and artifact.content_type is not None
        return Response(content=artifact.content, media_type=artifact.content_type, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(artifact.name, safe='')}"})


@router.post("/artifacts/{artifact_id}/preview", response_model=ArtifactPreviewResponse)
def build_artifact_preview(
    artifact_id: str, user: User = Depends(current_user)
) -> ArtifactPreviewResponse:
    """Render the first pages of an already-generated export.

    Read-only: it previews bytes that exist, creates no artifact and changes
    no checksum. Authorisation is exactly the download path's — a preview must
    never be a way to see a file you may not download.
    """
    with transaction() as db:
        artifact = usage.owned_artifact(db, artifact_id, user.id)
        if not artifact:
            raise _not_found()
        if artifact.status != "ready" or not usage.has_downloadable_snapshot(artifact):
            raise HTTPException(status_code=409, detail="成果当前不可预览，请重新生成")
        if not artifact_preview.is_previewable(artifact.content_type):
            return ArtifactPreviewResponse(
                state=artifact_preview.STATE_UNAVAILABLE,
                pages=0,
                message="该格式暂不支持预览，可直接下载查看",
            )
        assert artifact.content is not None
        content = artifact.content
        name = artifact.name

    # Conversion is bounded but slow; keep it outside the DB transaction.
    try:
        pages = artifact_preview.generate(content, name)
    except artifact_preview.ArtifactPreviewUnavailable as exc:
        return ArtifactPreviewResponse(
            state=artifact_preview.STATE_UNAVAILABLE, pages=0, message=exc.message
        )
    return ArtifactPreviewResponse(state=artifact_preview.STATE_READY, pages=pages)


@router.get("/artifacts/{artifact_id}/preview/{page}", response_class=Response)
def read_artifact_preview(
    artifact_id: str, page: int, user: User = Depends(current_user)
) -> Response:
    with transaction() as db:
        artifact = usage.owned_artifact(db, artifact_id, user.id)
        if not artifact:
            raise _not_found()
        if artifact.content is None:
            raise _not_found()
        digest = artifact_preview.cache_key(artifact.content)
    image = artifact_preview.read_page(digest, page)
    if image is None:
        raise HTTPException(status_code=404, detail="预览尚未生成")
    return Response(
        content=image,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/artifacts/{artifact_id}:regenerate", response_model=ArtifactResponse)
def regenerate_artifact(artifact_id: str, user: User = Depends(current_user)) -> ArtifactResponse:
    with transaction() as db:
        artifact = usage.owned_artifact(db, artifact_id, user.id)
        if not artifact:
            raise _not_found()
        if artifact.status != "expired":
            raise HTTPException(status_code=409, detail="仅已过期成果可重新生成")
        project = db.get(WritingProject, artifact.project_id)
        if project is None:
            raise _not_found()
        blockers = writing.unsupported_claim_ids(db, project.id)
        if blockers:
            raise HTTPException(
                status_code=409,
                detail=f"存在未获支持的 Claim：{', '.join(blockers)}",
            )
        manifest = usage.artifact_manifest(artifact)
        if manifest.get("presentation_style") not in (None, "template", "inspired"):
            raise HTTPException(status_code=409, detail="原演示稿样式不可识别，请从项目重新导出")
        template = manifest.get("template") or {}
        if (
            isinstance(template, dict) and template.get("mode") not in (None, "builtin")
        ) or manifest.get("presentation_style") == "inspired":
            raise HTTPException(
                status_code=410,
                detail="演示模板库已停用，历史成果仍可下载；请从文稿重新普通导出。",
            )
        operation = writing.start_operation(
            db, writing.create_operation(db, project, "export")
        )
        from app.services.writing_scope import WritingScopeUnavailableError

        try:
            regenerated = usage.regenerate_export_artifact(
                db,
                user=user,
                artifact=artifact,
                project=project,
                operation=operation,
            )
        except WritingScopeUnavailableError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        writing.complete_operation(
            db,
            operation,
            {
                "artifact_id": artifact.id,
                "format": usage.artifact_manifest(regenerated).get("format", "docx"),
                "regenerated": True,
                "evidence_sources": usage.artifact_manifest(regenerated).get("evidence_sources", []),
            },
        )
        return _artifact_response(regenerated)
