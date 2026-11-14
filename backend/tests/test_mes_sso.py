"""MES 单点登录票据：验签、过期、格式与未配置四类边界。"""
from __future__ import annotations

import base64
import json
import time

import pytest

from app.core.config import settings
from app.services import mes_sso

SECRET = "unit-test-secret"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "mes_sso_secret", SECRET)


def _payload(**overrides):
    now = int(time.time())
    payload = {
        "sub": "admin",
        "name": "管理员",
        "role": "admin",
        "admin": True,
        "iat": now,
        "exp": now + 60,
        "jti": "abc123",
    }
    payload.update(overrides)
    return payload


def test_签发的票据可以验回同一个身份():
    ticket = mes_sso.sign(_payload())
    identity = mes_sso.verify_ticket(ticket)
    assert identity == {
        "username": "admin",
        "real_name": "管理员",
        "role_code": "admin",
        "is_admin": True,
    }


def test_姓名缺省时回落到账号():
    identity = mes_sso.verify_ticket(mes_sso.sign({"sub": "zhangsan", "exp": int(time.time()) + 60}))
    assert identity["username"] == "zhangsan"
    assert identity["real_name"] == "zhangsan"
    assert identity["is_admin"] is False


def test_换密钥签的票据验不过():
    ticket = mes_sso.sign(_payload(), secret="another-secret")
    with pytest.raises(mes_sso.MesSsoError, match="签名不匹配"):
        mes_sso.verify_ticket(ticket)


def test_改动载荷会破坏签名():
    ticket = mes_sso.sign(_payload())
    body, signature = ticket.split(".")
    forged = base64.urlsafe_b64encode(
        json.dumps({"sub": "root", "exp": int(time.time()) + 60}).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    with pytest.raises(mes_sso.MesSsoError, match="签名不匹配"):
        mes_sso.verify_ticket(forged + "." + signature)


def test_过期票据被拒():
    with pytest.raises(mes_sso.MesSsoError, match="已过期"):
        mes_sso.verify_ticket(mes_sso.sign(_payload(exp=int(time.time()) - 30)))


def test_刚过期几秒内仍容许时钟漂移():
    ticket = mes_sso.sign(_payload(exp=int(time.time()) - 2))
    assert mes_sso.verify_ticket(ticket)["username"] == "admin"


def test_缺有效期与缺账号都算不合法():
    with pytest.raises(mes_sso.MesSsoError, match="有效期"):
        mes_sso.verify_ticket(mes_sso.sign({"sub": "admin"}))
    with pytest.raises(mes_sso.MesSsoError, match="账号"):
        mes_sso.verify_ticket(mes_sso.sign({"exp": int(time.time()) + 60}))


def test_格式不对直接拒():
    for bad in ["", "abc", "a.b.c", ".sig", "body."]:
        with pytest.raises(mes_sso.MesSsoError):
            mes_sso.verify_ticket(bad)


def test_未配置密钥时入口关闭(monkeypatch):
    monkeypatch.setattr(settings, "mes_sso_secret", "")
    assert mes_sso.enabled() is False
    with pytest.raises(mes_sso.MesSsoError, match="未配置"):
        mes_sso.verify_ticket(mes_sso.sign(_payload(), secret=SECRET))
