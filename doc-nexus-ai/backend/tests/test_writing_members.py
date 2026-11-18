"""Writing project collaboration: roles, ACL layering and chapter concurrency.

Covers the two rules that make shared writing safe:

* a project role decides what you may do to the document;
* it never grants knowledge-space access — that is re-checked per acting user.

Plus the optimistic-concurrency contract that keeps two editors from silently
overwriting each other.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.main import app
from app.repositories import writing
from tests._auth import bootstrap_admin_response


def _admin_headers(client: TestClient) -> dict[str, str]:
    response = bootstrap_admin_response(client)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _member_login(client: TestClient, username: str) -> tuple[dict[str, str], dict]:
    invited = client.post(
        "/admin/users",
        headers=_admin_headers(client),
        json={
            "username": username,
            "email": f"{username}@example.com",
            "display_name": username,
        },
    )
    assert invited.status_code == 201, invited.text
    accepted = client.post(
        f"/auth/invitations/{invited.json()['invite_token']}/accept",
        json={
            "password": "Member-password-123",
            "password_confirmation": "Member-password-123",
        },
    )
    assert accepted.status_code == 200, accepted.text
    login = client.post(
        "/auth/login",
        json={"username": username, "password": "Member-password-123"},
    )
    assert login.status_code == 200, login.text
    return (
        {"Authorization": f"Bearer {login.json()['access_token']}"},
        login.json()["user"],
    )


def _space_with_reader(client: TestClient, name: str, user_id: str) -> str:
    space = client.post(
        "/knowledge-spaces",
        headers=_admin_headers(client),
        json={"name": name, "description": ""},
    )
    assert space.status_code == 201, space.text
    space_id = space.json()["id"]
    granted = client.post(
        f"/knowledge-spaces/{space_id}/members",
        headers=_admin_headers(client),
        json={"user_id": user_id, "role": "reader"},
    )
    assert granted.status_code == 201, granted.text
    return space_id


def _grant_reader(client: TestClient, space_id: str, user_id: str) -> None:
    granted = client.post(
        f"/knowledge-spaces/{space_id}/members",
        headers=_admin_headers(client),
        json={"user_id": user_id, "role": "reader"},
    )
    assert granted.status_code == 201, granted.text


def _client_database(tmp_path: Path, name: str):
    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / name
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    return old_path, old_url, path


def _restore_database(old_path: Path, old_url: str, path: Path) -> None:
    settings.db_path, settings.database_url = old_path, old_url
    database.reset_database(path)


def _project_with_chapter(
    client: TestClient, headers: dict[str, str], space_id: str, title: str
) -> tuple[str, str]:
    created = client.post(
        "/writing/projects",
        headers=headers,
        json={"title": title, "space_ids": [space_id]},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    outline = client.put(
        f"/writing/projects/{project_id}/outline",
        headers=headers,
        json={"chapters": [{"title": "第一章"}]},
    )
    assert outline.status_code == 200, outline.text
    chapter_id = outline.json()["outline"]["chapters"][0]["id"]
    return project_id, chapter_id


def _add_member(
    client: TestClient,
    owner: dict[str, str],
    project_id: str,
    user_id: str,
    role: str,
) -> str:
    added = client.post(
        f"/writing/projects/{project_id}/members",
        headers=owner,
        json={"user_id": user_id, "role": role},
    )
    assert added.status_code == 201, added.text
    return added.json()["id"]


def test_member_directory_is_owner_only_and_returns_public_profiles(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-directory.sqlite3")
    try:
        with TestClient(app) as client:
            owner_headers, owner = _member_login(client, "directory-owner")
            editor_headers, editor = _member_login(client, "directory-editor")
            outsider_headers, _ = _member_login(client, "directory-outsider")
            space_id = _space_with_reader(client, "directory-space", owner["id"])
            project_id, _ = _project_with_chapter(
                client, owner_headers, space_id, "Private directory project"
            )
            _add_member(client, owner_headers, project_id, editor["id"], "editor")
            url = f"/writing/projects/{project_id}/member-directory"
            response = client.get(url, params={"q": "directory-editor"}, headers=owner_headers)
            assert response.status_code == 200, response.text
            assert response.json() == {
                "items": [{
                    "id": editor["id"],
                    "username": "directory-editor",
                    "display_name": "directory-editor",
                }]
            }
            for headers in (editor_headers, outsider_headers):
                denied = client.get(url, headers=headers)
                assert denied.status_code == 404
                assert editor["id"] not in denied.text
            disabled = client.delete(
                f"/admin/users/{editor['id']}", headers=_admin_headers(client)
            )
            assert disabled.status_code == 200, disabled.text
            assert client.get(
                url, params={"q": "directory-editor"}, headers=owner_headers
            ).json() == {"items": []}
    finally:
        _restore_database(old_path, old_url, path)


def test_project_roles_gate_reads_writes_and_member_management(tmp_path: Path):
    old_path, old_url, path = _client_database(
        tmp_path, "writing-members-roles.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner_headers, owner_user = _member_login(client, "wm-roles-owner")
            editor_headers, editor_user = _member_login(client, "wm-roles-editor")
            commenter_headers, commenter_user = _member_login(
                client, "wm-roles-commenter"
            )
            viewer_headers, viewer_user = _member_login(client, "wm-roles-viewer")
            outsider_headers, _ = _member_login(client, "wm-roles-outsider")

            space_id = _space_with_reader(client, "wm-roles-space", owner_user["id"])
            for user in (editor_user, commenter_user, viewer_user):
                _grant_reader(client, space_id, user["id"])

            project_id, chapter_id = _project_with_chapter(
                client, owner_headers, space_id, "角色矩阵文稿"
            )

            _add_member(client, owner_headers, project_id, editor_user["id"], "editor")
            _add_member(
                client, owner_headers, project_id, commenter_user["id"], "commenter"
            )
            _add_member(client, owner_headers, project_id, viewer_user["id"], "viewer")

            # Everyone with a role can read the document.
            for headers in (editor_headers, commenter_headers, viewer_headers):
                read = client.get(f"/writing/projects/{project_id}", headers=headers)
                assert read.status_code == 200, read.text
                assert read.json()["title"] == "角色矩阵文稿"

            # A non-member gets 404 — existence and title are not disclosed.
            outsider = client.get(
                f"/writing/projects/{project_id}", headers=outsider_headers
            )
            assert outsider.status_code == 404
            assert "角色矩阵文稿" not in outsider.text

            # Only editor and above may write the body.
            edited = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=editor_headers,
                json={"content": "编辑者写入的正文"},
            )
            assert edited.status_code == 201, edited.text

            for headers in (commenter_headers, viewer_headers, outsider_headers):
                blocked = client.put(
                    f"/writing/projects/{project_id}/chapters/{chapter_id}",
                    headers=headers,
                    json={"content": "不应写入"},
                )
                assert blocked.status_code == 404, blocked.text

            # Member management is owner-only.
            for headers in (editor_headers, commenter_headers, viewer_headers):
                denied = client.post(
                    f"/writing/projects/{project_id}/members",
                    headers=headers,
                    json={"user_id": viewer_user["id"], "role": "viewer"},
                )
                assert denied.status_code == 404, denied.text

            # Deleting the project is owner-only too.
            denied_delete = client.delete(
                f"/writing/projects/{project_id}", headers=editor_headers
            )
            assert denied_delete.status_code == 404, denied_delete.text
    finally:
        _restore_database(old_path, old_url, path)


def test_removed_member_loses_access_on_the_next_request(tmp_path: Path):
    old_path, old_url, path = _client_database(
        tmp_path, "writing-members-removal.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner_headers, owner_user = _member_login(client, "wm-remove-owner")
            editor_headers, editor_user = _member_login(client, "wm-remove-editor")
            space_id = _space_with_reader(client, "wm-remove-space", owner_user["id"])
            _grant_reader(client, space_id, editor_user["id"])

            project_id, _ = _project_with_chapter(
                client, owner_headers, space_id, "可撤销成员"
            )
            member_id = _add_member(
                client, owner_headers, project_id, editor_user["id"], "editor"
            )

            assert (
                client.get(
                    f"/writing/projects/{project_id}", headers=editor_headers
                ).status_code
                == 200
            )

            removed = client.delete(
                f"/writing/projects/{project_id}/members/{member_id}",
                headers=owner_headers,
            )
            assert removed.status_code == 204, removed.text

            after = client.get(
                f"/writing/projects/{project_id}", headers=editor_headers
            )
            assert after.status_code == 404

            # Re-inviting reuses the row and restores access.
            _add_member(
                client, owner_headers, project_id, editor_user["id"], "commenter"
            )
            restored = client.get(
                f"/writing/projects/{project_id}", headers=editor_headers
            )
            assert restored.status_code == 200
            members = client.get(
                f"/writing/projects/{project_id}/members", headers=editor_headers
            )
            assert members.status_code == 200, members.text
            assert members.json()["my_role"] == "commenter"
            assert members.json()["owner"]["user_id"] == owner_user["id"]
            assert len(members.json()["items"]) == 1
    finally:
        _restore_database(old_path, old_url, path)


def test_membership_does_not_grant_knowledge_space_access(tmp_path: Path):
    """A shared document must not become a way around the space ACL."""
    old_path, old_url, path = _client_database(tmp_path, "writing-members-acl.sqlite3")
    try:
        with TestClient(app) as client:
            owner_headers, owner_user = _member_login(client, "wm-acl-owner")
            editor_headers, editor_user = _member_login(client, "wm-acl-editor")
            space_id = _space_with_reader(client, "wm-acl-space", owner_user["id"])
            # Deliberately NOT granting the editor access to the space.

            project_id, chapter_id = _project_with_chapter(
                client, owner_headers, space_id, "受限资料文稿"
            )
            _add_member(client, owner_headers, project_id, editor_user["id"], "editor")

            # The document body itself stays readable.
            assert (
                client.get(
                    f"/writing/projects/{project_id}", headers=editor_headers
                ).status_code
                == 200
            )

            # Source material does not.
            evidence = client.post(
                f"/writing/projects/{project_id}/evidence:expand",
                headers=editor_headers,
                json={"citation_id": "writing-evidence:ver:0-10"},
            )
            assert evidence.status_code == 403, evidence.text

            matched = client.post(
                f"/writing/projects/{project_id}/knowledge:match",
                headers=editor_headers,
                json={"query": "报价范围"},
            )
            assert matched.status_code == 403, matched.text

            # Saving the body is authorised by role, but the operation stops
            # because this user's knowledge scope is not available.
            saved = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=editor_headers,
                json={"content": "无空间权限的写入"},
            )
            assert saved.status_code == 201, saved.text
            assert saved.json()["status"] == "stopped"

            # The owner, who does hold space access, is unaffected.
            owner_saved = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=owner_headers,
                json={"content": "所有者的写入"},
            )
            assert owner_saved.status_code == 201, owner_saved.text
            assert owner_saved.json()["status"] == "completed"
    finally:
        _restore_database(old_path, old_url, path)


def test_chapter_save_conflict_returns_both_versions(tmp_path: Path):
    old_path, old_url, path = _client_database(
        tmp_path, "writing-members-conflict.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner_headers, owner_user = _member_login(client, "wm-conflict-owner")
            editor_headers, editor_user = _member_login(client, "wm-conflict-editor")
            space_id = _space_with_reader(client, "wm-conflict-space", owner_user["id"])
            _grant_reader(client, space_id, editor_user["id"])

            project_id, chapter_id = _project_with_chapter(
                client, owner_headers, space_id, "并发保存文稿"
            )
            _add_member(client, owner_headers, project_id, editor_user["id"], "editor")

            loaded = client.get(
                f"/writing/projects/{project_id}", headers=owner_headers
            )
            assert loaded.status_code == 200
            base_revision = loaded.json()["outline"]["chapters"][0]["revision"]

            first = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=owner_headers,
                json={"content": "所有者先保存", "base_revision": base_revision},
            )
            assert first.status_code == 201, first.text

            # The editor still holds the stale revision.
            second = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=editor_headers,
                json={"content": "编辑者的并发保存", "base_revision": base_revision},
            )
            assert second.status_code == 409, second.text
            detail = second.json()["detail"]
            assert detail["error_code"] == "chapter_revision_conflict"
            assert detail["current_content"] == "所有者先保存"
            assert detail["submitted_content"] == "编辑者的并发保存"
            assert detail["current_revision"] > base_revision

            # Retrying from the reported revision succeeds.
            retried = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=editor_headers,
                json={
                    "content": "编辑者的并发保存",
                    "base_revision": detail["current_revision"],
                },
            )
            assert retried.status_code == 201, retried.text

            # A client that sends no base_revision keeps the legacy behaviour.
            legacy = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=owner_headers,
                json={"content": "旧客户端保存"},
            )
            assert legacy.status_code == 201, legacy.text
    finally:
        _restore_database(old_path, old_url, path)


def test_stale_loaded_session_cannot_overwrite_a_committed_chapter(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-stale-session.sqlite3")
    try:
        with TestClient(app) as client:
            headers, owner = _member_login(client, "stale-session-owner")
            space_id = _space_with_reader(client, "stale-session-space", owner["id"])
            project_id, chapter_id = _project_with_chapter(client, headers, space_id, "Race")
            with database.SessionLocal() as first, database.SessionLocal() as second:
                first_project = writing.project_by_id(first, project_id)
                stale_project = writing.project_by_id(second, project_id)
                assert first_project is not None and stale_project is not None
                stale_chapter = stale_project.outline.chapters[0]
                revision = stale_chapter.revision
                writing.save_chapter(
                    first, first_project, chapter_id, "Committed first",
                    base_revision=revision, editor_user_id=owner["id"],
                )
                first.commit()
                with pytest.raises(writing.ChapterRevisionConflict) as caught:
                    writing.save_chapter(
                        second, stale_project, chapter_id, "Must not overwrite",
                        base_revision=revision, editor_user_id=owner["id"],
                    )
                assert caught.value.chapter.content == "Committed first"
                assert caught.value.chapter.revision == revision + 1
                second.rollback()
            loaded = client.get(f"/writing/projects/{project_id}", headers=headers).json()
            assert loaded["outline"]["chapters"][0]["content"] == "Committed first"
            assert [item["content"] for item in loaded["versions"]] == ["Committed first"]
    finally:
        _restore_database(old_path, old_url, path)


def test_chapter_edit_lock_is_advisory(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-members-lock.sqlite3")
    try:
        with TestClient(app) as client:
            owner_headers, owner_user = _member_login(client, "wm-lock-owner")
            editor_headers, editor_user = _member_login(client, "wm-lock-editor")
            space_id = _space_with_reader(client, "wm-lock-space", owner_user["id"])
            _grant_reader(client, space_id, editor_user["id"])

            project_id, chapter_id = _project_with_chapter(
                client, owner_headers, space_id, "软锁文稿"
            )
            _add_member(client, owner_headers, project_id, editor_user["id"], "editor")

            taken = client.post(
                f"/writing/projects/{project_id}/chapters/{chapter_id}/edit-lock",
                headers=owner_headers,
                json={"ttl_seconds": 120},
            )
            assert taken.status_code == 200, taken.text
            assert taken.json()["held_by_me"] is True
            assert taken.json()["edit_lock_user_id"] == owner_user["id"]

            # Another editor sees the holder but is not blocked from saving.
            contended = client.post(
                f"/writing/projects/{project_id}/chapters/{chapter_id}/edit-lock",
                headers=editor_headers,
                json={"ttl_seconds": 120},
            )
            assert contended.status_code == 200, contended.text
            assert contended.json()["held_by_me"] is False
            assert contended.json()["edit_lock_user_id"] == owner_user["id"]

            saved = client.put(
                f"/writing/projects/{project_id}/chapters/{chapter_id}",
                headers=editor_headers,
                json={"content": "软锁不阻断保存"},
            )
            assert saved.status_code == 201, saved.text

            released = client.delete(
                f"/writing/projects/{project_id}/chapters/{chapter_id}/edit-lock",
                headers=owner_headers,
            )
            assert released.status_code == 200, released.text
            assert released.json()["edit_lock_user_id"] is None
    finally:
        _restore_database(old_path, old_url, path)


def test_budget_limit_change_is_owner_only(tmp_path: Path):
    old_path, old_url, path = _client_database(
        tmp_path, "writing-members-budget.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner_headers, owner_user = _member_login(client, "wm-budget-owner")
            editor_headers, editor_user = _member_login(client, "wm-budget-editor")
            space_id = _space_with_reader(client, "wm-budget-space", owner_user["id"])
            _grant_reader(client, space_id, editor_user["id"])

            project_id, _ = _project_with_chapter(
                client, owner_headers, space_id, "预算文稿"
            )
            _add_member(client, owner_headers, project_id, editor_user["id"], "editor")

            # An editor may rename the project.
            renamed = client.patch(
                f"/writing/projects/{project_id}",
                headers=editor_headers,
                json={"title": "编辑者改的标题"},
            )
            assert renamed.status_code == 200, renamed.text
            assert renamed.json()["title"] == "编辑者改的标题"

            # But not move the money.
            denied = client.patch(
                f"/writing/projects/{project_id}",
                headers=editor_headers,
                json={"budget_limit": "99"},
            )
            assert denied.status_code == 403, denied.text

            allowed = client.patch(
                f"/writing/projects/{project_id}",
                headers=owner_headers,
                json={"budget_limit": "99"},
            )
            assert allowed.status_code == 200, allowed.text
            assert allowed.json()["budget_limit"] == "99"
    finally:
        _restore_database(old_path, old_url, path)


def test_shared_with_me_lists_participation_not_ownership(tmp_path: Path):
    old_path, old_url, path = _client_database(
        tmp_path, "writing-members-shared.sqlite3"
    )
    try:
        with TestClient(app) as client:
            owner_headers, owner_user = _member_login(client, "wm-shared-owner")
            editor_headers, editor_user = _member_login(client, "wm-shared-editor")
            space_id = _space_with_reader(client, "wm-shared-space", owner_user["id"])
            _grant_reader(client, space_id, editor_user["id"])

            project_id, _ = _project_with_chapter(
                client, owner_headers, space_id, "参与的文稿"
            )
            _add_member(client, owner_headers, project_id, editor_user["id"], "editor")

            mine = client.get("/writing/projects", headers=editor_headers)
            assert mine.status_code == 200, mine.text
            assert [item["id"] for item in mine.json()["items"]] == []

            shared = client.get(
                "/writing/projects:shared-with-me", headers=editor_headers
            )
            assert shared.status_code == 200, shared.text
            assert [item["id"] for item in shared.json()["items"]] == [project_id]
            shared_item = shared.json()["items"][0]
            assert shared_item["chapter_count"] == 1
            # 参与列表与自有列表共用计数版响应：正文与版本明细不随列表返回。
            assert {
                "outline",
                "versions",
                "claims",
                "operations",
                "brief",
            } & set(shared_item) == set()

            # The owner sees it in their own list, not in the shared one.
            owner_shared = client.get(
                "/writing/projects:shared-with-me", headers=owner_headers
            )
            assert owner_shared.status_code == 200
            assert owner_shared.json()["items"] == []
            owner_mine = client.get("/writing/projects", headers=owner_headers)
            assert [item["id"] for item in owner_mine.json()["items"]] == [project_id]
    finally:
        _restore_database(old_path, old_url, path)
