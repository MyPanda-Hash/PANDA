from __future__ import annotations

from pathlib import Path

import pytest

from app.core import database
from app.core.config import settings
from app.models.auth import User
from app.models.mes_binding import MesUserBinding
from app.services import org_map
from app.services.mes_client import MesWatchError


@pytest.fixture
def db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "org-map.sqlite3"
    database.reset_database(path)
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.initialize_database()
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()
        database.reset_database(path)
        settings.db_path, settings.database_url = original


@pytest.fixture(autouse=True)
def clear_cache():
    org_map.invalidate()
    yield
    org_map.invalidate()


class FakeMesClient:
    """只实现人力地图用得到的 org_map()。"""

    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload or {}
        self.error = error
        self.calls = 0

    def org_map(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.payload


PAYLOAD = {
    "departments": [
        {"id": 1, "parentId": 0, "name": "公司", "path": "公司", "depth": 0, "sort": 1},
        {
            "id": 5,
            "parentId": 1,
            "name": "生产部",
            "path": "公司 / 生产部",
            "depth": 1,
            "sort": 4,
        },
        {
            "id": 6,
            "parentId": 5,
            "name": "熔铸车间",
            "path": "公司 / 生产部 / 熔铸车间",
            "depth": 2,
            "sort": 1,
        },
    ],
    "users": [
        {
            "userName": "worker",
            "realName": "赵车间",
            "deptId": 6,
            "deptName": "熔铸车间",
            "deptPath": "公司 / 生产部 / 熔铸车间",
            "roleName": "车间员工",
        },
        {
            "userName": "admin",
            "realName": "管理员",
            "deptId": 1,
            "deptName": "公司",
            "deptPath": "公司",
            "roleName": "超级管理员",
        },
    ],
}


def _user(db, user_id: str, username: str) -> User:
    from datetime import datetime, timezone

    user = User(
        id=user_id,
        username=username,
        email=f"{user_id}@example.com",
        display_name=user_id,
        password_hash="x",
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    return user


def test_snapshot_normalizes_mes_payload():
    snap = org_map.snapshot(client=FakeMesClient(PAYLOAD), ttl=60)

    assert [row["name"] for row in snap["departments"]] == ["公司", "生产部", "熔铸车间"]
    assert snap["departments"][2]["parent_id"] == 5
    assert snap["users"]["worker"]["dept_path"] == "公司 / 生产部 / 熔铸车间"
    assert snap["users"]["worker"]["position"] == "车间员工"


def test_snapshot_degrades_instead_of_raising():
    snap = org_map.snapshot(client=FakeMesClient(error=MesWatchError("MES 开放接口未启用")))

    assert snap == {"departments": [], "users": {}}


def test_snapshot_is_cached_until_invalidated():
    client = FakeMesClient(PAYLOAD)
    first = org_map.snapshot(client=client)
    second = org_map.snapshot(client=client)

    assert first["departments"] == second["departments"]
    assert client.calls == 1, "60 秒内只允许打一次 MES"

    org_map.invalidate()
    org_map.snapshot(client=client)
    assert client.calls == 2, "失效后必须重新取"


def test_fields_follow_binding_not_username(db):
    user = _user(db, "usr_1", "zhang")
    db.add(
        MesUserBinding(
            id="mesb_1",
            user_id="usr_1",
            mes_username="worker",
            mes_password="",
            mes_role_code="WORKER",
            mes_is_admin=False,
        )
    )
    db.commit()

    fields = org_map.fields_of(db, user, snap=org_map.snapshot(client=FakeMesClient(PAYLOAD)))

    assert fields["dept_name"] == "熔铸车间", "有绑定就按绑定账号的部门算，不按用户名猜"
    assert fields["dept_path"] == "公司 / 生产部 / 熔铸车间"


def test_fields_fall_back_to_same_username(db):
    user = _user(db, "usr_2", "admin")

    fields = org_map.fields_of(db, user, snap=org_map.snapshot(client=FakeMesClient(PAYLOAD)))

    assert fields["dept_name"] == "公司", "没绑定但同名时按同名账号取部门"


def test_unknown_account_is_unassigned(db):
    user = _user(db, "usr_3", "ghost")

    fields = org_map.fields_of(db, user, snap=org_map.snapshot(client=FakeMesClient(PAYLOAD)))

    assert fields == org_map.NO_DEPARTMENT


def test_group_map_places_members_and_lists_unassigned(db):
    worker = _user(db, "usr_worker", "worker")
    ghost = _user(db, "usr_ghost", "ghost")
    snap = org_map.snapshot(client=FakeMesClient(PAYLOAD))

    payload = org_map.group_map(db, [worker.id, ghost.id], snap=snap)

    by_id = {row["id"]: row for row in payload["departments"]}
    assert by_id[6]["member_ids"] == [worker.id]
    assert by_id[1]["member_ids"] == []
    assert payload["unassigned"] == [ghost.id]
    assert payload["members"][worker.id]["dept_name"] == "熔铸车间"
