"""Human suggestions on a shared writing project."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from tests.test_writing_members import (
    _add_member,
    _client_database,
    _grant_reader,
    _member_login,
    _project_with_chapter,
    _restore_database,
    _space_with_reader,
)


def _save_chapter(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    chapter_id: str,
    content: str,
) -> None:
    saved = client.put(
        f"/writing/projects/{project_id}/chapters/{chapter_id}",
        headers=headers,
        json={"content": content},
    )
    assert saved.status_code == 201, saved.text


def test_human_suggestion_uses_the_candidate_lifecycle(tmp_path: Path):
    old_path, old_url, path = _client_database(tmp_path, "writing-suggestions.sqlite3")
    try:
        with TestClient(app) as client:
            owner_headers, owner_user = _member_login(client, "ws-owner")
            commenter_headers, commenter_user = _member_login(client, "ws-commenter")
            space_id = _space_with_reader(client, "ws-space", owner_user["id"])
            _grant_reader(client, space_id, commenter_user["id"])

            project_id, chapter_id = _project_with_chapter(
                client, owner_headers, space_id, "建议文稿"
            )
            _add_member(
                client, owner_headers, project_id, commenter_user["id"], "commenter"
            )
            _save_chapter(client, owner_headers, project_id, chapter_id, "原始正文。")

            # A commenter proposes without writing the body.
            suggested = client.post(
                f"/writing/projects/{project_id}/suggestions",
                headers=commenter_headers,
                json={"chapter_id": chapter_id, "proposed_content": "建议后的正文。"},
            )
            assert suggested.status_code == 201, suggested.text
            candidate = suggested.json()
            assert candidate["origin"] == "human"
            assert candidate["status"] == "pending"
            assert candidate["author_user_id"] == commenter_user["id"]

            project = client.get(
                f"/writing/projects/{project_id}", headers=owner_headers
            ).json()
            assert project["outline"]["chapters"][0]["content"] == "原始正文。"

            # It appears alongside agent candidates in the same list.
            listed = client.get(
                f"/writing/projects/{project_id}/section-candidates",
                headers=owner_headers,
            )
            assert listed.status_code == 200, listed.text
            assert [item["id"] for item in listed.json()] == [candidate["id"]]

            # A commenter cannot accept their own suggestion.
            denied = client.post(
                f"/writing/projects/{project_id}/section-candidates/{candidate['id']}:publish",
                headers=commenter_headers,
            )
            assert denied.status_code == 404, denied.text

            # An editor accepts it through the existing candidate endpoint.
            published = client.post(
                f"/writing/projects/{project_id}/section-candidates/{candidate['id']}:publish",
                headers=owner_headers,
            )
            assert published.status_code == 200, published.text
            assert published.json()["content"] == "建议后的正文。"

            after = client.get(
                f"/writing/projects/{project_id}", headers=owner_headers
            ).json()
            assert after["outline"]["chapters"][0]["content"] == "建议后的正文。"

            # Rejection keeps the record.
            second = client.post(
                f"/writing/projects/{project_id}/suggestions",
                headers=commenter_headers,
                json={"chapter_id": chapter_id, "proposed_content": "另一个建议。"},
            )
            assert second.status_code == 201, second.text
            rejected = client.post(
                f"/writing/projects/{project_id}/section-candidates/{second.json()['id']}:reject",
                headers=owner_headers,
            )
            assert rejected.status_code == 200, rejected.text
            assert rejected.json()["status"] == "rejected"
            assert rejected.json()["origin"] == "human"
    finally:
        _restore_database(old_path, old_url, path)
