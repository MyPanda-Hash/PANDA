"""Shared pytest fixtures.

Most suites exercise Doc Nexus in isolation and must not reach the
LinkMind/Landing integration. The .env may enable LinkMind; disable it
globally so default model endpoints resolve without real outbound HTTP.
LinkMind integration tests opt back in via _configure_linkmind().
"""

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _isolate_runtime_integrations() -> None:
    original_linkmind_enabled = settings.linkmind_enabled
    settings.linkmind_enabled = False
    try:
        yield
    finally:
        settings.linkmind_enabled = original_linkmind_enabled


@pytest.fixture(autouse=True)
def _allow_media_egress_in_tests(monkeypatch) -> None:
    """测试默认放行图片出网。

    生产默认拦截「把图片发往云端视觉模型」（图片无法脱敏）；
    视觉相关用例只验证协议与解析，不涉及数据策略，这里显式放行。
    数据策略本身由 tests/test_data_guard.py 覆盖。
    """
    monkeypatch.setenv("DOC_NEXUS_DATA_MASK_MEDIA_POLICY", "allow")


@pytest.fixture
def seed_writing_publication():
    """仅在隔离测试库直接还原历史快照，不恢复已下线的发布能力。"""
    import hashlib
    import json

    from app.core import database
    from app.core.security import new_token, now
    from app.models.writing import WritingProject, WritingProjectPublication

    def seed(project_id, *, share_scope="all_internal", allow_cite=True, evidence_sources=None):
        with database.transaction() as db:
            project = db.get(WritingProject, project_id)
            assert project is not None and project.outline is not None
            sources = evidence_sources or []
            snapshot = json.dumps({
                "outline_status": project.outline.status,
                "chapters": [
                    {"id": chapter.id, "position": chapter.position,
                     "title": chapter.title, "content": chapter.content}
                    for chapter in sorted(project.outline.chapters, key=lambda item: item.position)
                ],
                "evidence_sources": sources,
            }, ensure_ascii=False)
            publication_id = f"wpp_{new_token(10)}"
            stamp = now()
            db.add(WritingProjectPublication(
                id=publication_id,
                source_project_id=project.id,
                source_owner_user_id=project.owner_user_id,
                shared_by_user_id=project.owner_user_id,
                title=project.title,
                summary="历史快照",
                version=1,
                snapshot_json=snapshot,
                content_hash=hashlib.sha256(snapshot.encode("utf-8")).hexdigest(),
                share_scope=share_scope,
                space_ids_json=project.selected_space_ids,
                citation_refs_json=json.dumps({
                    "items": [source.get("citation_id") or source["id"] for source in sources],
                    "truncated": False,
                }),
                allow_cite=allow_cite,
                allow_remix=True,
                status="active",
                published_at=stamp,
                created_at=stamp,
            ))
        return publication_id

    return seed

@pytest.fixture(autouse=True)
def _isolate_upload_dir(tmp_path, monkeypatch) -> None:
    """测试不许往真实的 uploads/ 写文件。

    之前群聊附件测试用临时数据库，但 settings.upload_dir 还指向
    backend/uploads，跑一次全量就留下几十个 grp_* 目录。
    """
    # 用独立目录名，且不预先创建：有的用例自己会 mkdir("uploads")，
    # 我们先建好反而会让它抛 FileExistsError。
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "isolated-uploads")

@pytest.fixture(autouse=True)
def _disable_mes_watch_in_tests(monkeypatch) -> None:
    """测试绝不真的去轮询 MES。

    后台 worker 一旦按 .env 的 MES_WATCH_ENABLED=1 启动，就会在 TestClient
    生命周期里向 127.0.0.1:8080 发起请求并写库，整套测试会被拖到跑不完。
    """
    monkeypatch.setattr(settings, "mes_watch_enabled", False)

@pytest.fixture(autouse=True)
def _disable_mes_open_api_in_tests(monkeypatch) -> None:
    """测试不连真实 MES 开放接口；需要的用例自己 monkeypatch 成 True。"""
    monkeypatch.setattr(settings, "mes_open_api_enabled", False)
