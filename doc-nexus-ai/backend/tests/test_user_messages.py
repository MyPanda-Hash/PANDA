import pytest

from app.api.documents import public_failure_message
from app.core.user_messages import (
    is_user_safe_message,
    public_error_message,
    redact_inline_secrets,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Cookie: session=secret-cookie; refresh=secret-refresh", "Cookie: [REDACTED]"),
        ("Set-Cookie: sid=secret-cookie; Path=/", "Set-Cookie: [REDACTED]"),
        ("Authorization: Bearer header-secret", "Authorization: [REDACTED]"),
        (
            'headers={"Cookie": "session=secret-cookie; refresh=secret-refresh"}',
            'headers={"Cookie": "[REDACTED]"}',
        ),
        (
            "postgresql://db-user:db-password@db.internal:5432/app",
            "postgresql://[REDACTED]@db.internal:5432/app",
        ),
        (
            "https://alice:http-password@example.com/private",
            "https://[REDACTED]@example.com/private",
        ),
        (
            "https://host/path?X-Amz-Signature=sig-secret&X-Amz-Credential=cred-secret",
            "https://host/path?X-Amz-Signature=[REDACTED]&X-Amz-Credential=[REDACTED]",
        ),
        (
            "https://host/path?sig=sig-secret&signature=signature-secret&access_token=access-secret",
            "https://host/path?sig=[REDACTED]&signature=[REDACTED]&access_token=[REDACTED]",
        ),
        (
            "-----BEGIN PRIVATE KEY-----\nprivate-key-material\n-----END PRIVATE KEY-----",
            "[REDACTED]",
        ),
        (
            "API_KEY=api-secret AWS_ACCESS_KEY_ID=aws-secret CLIENT_SECRET=client-secret "
            "REFRESH_TOKEN=refresh-secret",
            "API_KEY=[REDACTED] AWS_ACCESS_KEY_ID=[REDACTED] CLIENT_SECRET=[REDACTED] "
            "REFRESH_TOKEN=[REDACTED]",
        ),
    ],
)
def test_redact_inline_secrets_covers_common_credential_strings(raw, expected):
    assert redact_inline_secrets(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CLIENT_SECRET=ab]cd", "CLIENT_SECRET=[REDACTED]"),
        ("CLIENT_SECRET=ab)cd", "CLIENT_SECRET=[REDACTED]"),
        ("CLIENT_SECRET=ab}cd#tail next", "CLIENT_SECRET=[REDACTED] next"),
        (
            "AUTHORIZATION=Bearer bearer-secret}tail",
            "AUTHORIZATION=[REDACTED]",
        ),
        (
            'CLIENT_SECRET="ab]cd" next',
            'CLIENT_SECRET="[REDACTED]" next',
        ),
        (
            "https://host/path?sig=ab)cd&next=visible",
            "https://host/path?sig=[REDACTED]&next=visible",
        ),
        (
            "oauth_signature=oauth-secret X-Goog-Signature=goog-secret "
            "x-oss-signature=oss-secret",
            "oauth_signature=[REDACTED] X-Goog-Signature=[REDACTED] "
            "x-oss-signature=[REDACTED]",
        ),
        ("-----BEGIN PRIVATE KEY-----\npartial-key-material", "[REDACTED]"),
        (
            "runner --api_key=cli-secret --mode=visible",
            "runner --api_key=[REDACTED] --mode=visible",
        ),
        ("CLIENT_SECRET=[ab]cd", "CLIENT_SECRET=[REDACTED]"),
        ("CLIENT_SECRET={ab}cd", "CLIENT_SECRET=[REDACTED]"),
        ('client_secret=["ab]cd"]', "client_secret=[REDACTED]"),
        (
            "https://host/path?sig=ab]cd&next=visible",
            "https://host/path?sig=[REDACTED]&next=visible",
        ),
    ],
)
def test_redact_inline_secrets_consumes_delimited_and_extended_credential_values(
    raw, expected
):
    assert redact_inline_secrets(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "runner --api-key cli-secret",
            "runner --api-key [REDACTED]",
        ),
        (
            "runner --token cli-secret!@#$%^&*()",
            "runner --token [REDACTED]",
        ),
        (
            "runner --api-key=cli-secret!@#$%^&*()",
            "runner --api-key=[REDACTED]",
        ),
        (
            'runner --token "cli secret" --mode visible',
            'runner --token "[REDACTED]" --mode visible',
        ),
        (
            "runner --token --mode visible",
            "runner --token --mode visible",
        ),
        (
            "runner --tokenize cli-secret --mode visible",
            "runner --tokenize cli-secret --mode visible",
        ),
    ],
)
def test_redact_inline_secrets_supports_space_separated_cli_credentials(
    raw, expected
):
    assert redact_inline_secrets(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "answer\napi_key=\nsecret\n普通尾巴",
            "answer\napi_key=\n[REDACTED]\n普通尾巴",
        ),
        (
            "answer api_key=\nsecret",
            "answer api_key=\n[REDACTED]",
        ),
    ],
)
def test_redact_inline_secrets_masks_dangling_assignment_continuations(
    raw, expected
):
    assert redact_inline_secrets(raw) == expected


def test_public_error_message_redacts_safe_text_and_fallback_before_returning():
    assert (
        public_error_message(
            "请求失败，请重试 api_key=public-api-secret",
            fallback="操作失败",
        )
        == "请求失败，请重试 api_key=[REDACTED]"
    )
    assert (
        public_error_message(
            "",
            fallback="操作失败 api_key=fallback-api-secret",
        )
        == "操作失败 api_key=[REDACTED]"
    )


def test_redact_inline_secrets_keeps_non_secret_token_statistics():
    raw = (
        "token_stats={'input': 2} token_metrics={'total': 3} "
        "signature_algorithm=HMAC session_id=session-1"
    )

    assert redact_inline_secrets(raw) == raw


def test_redact_inline_secrets_preserves_escaped_newline_tail():
    raw = '{"text":"api_key=inline-secret\\n普通尾巴"}'

    assert redact_inline_secrets(raw) == '{"text":"api_key=[REDACTED]\\n普通尾巴"}'


def test_public_error_message_keeps_chinese_guidance():
    assert (
        public_error_message("文档解析失败，请重试。", fallback="操作失败")
        == "文档解析失败，请重试。"
    )


def test_public_error_message_hides_traceback_and_exception_names():
    assert is_user_safe_message("RuntimeError") is False
    assert (
        public_error_message("RuntimeError", fallback="写作任务失败，请稍后重试。")
        == "写作任务失败，请稍后重试。"
    )
    dump = 'Traceback (most recent call last):\n  File "/app/backend/app/api/documents.py", line 1'
    assert public_error_message(dump, fallback="操作失败") == "操作失败"


def test_public_failure_message_hides_embedding_connection_refused():
    stored = (
        "1/1 chunks failed after 3 attempts: chunk 0 failed: "
        "[Errno 111] Connection refused"
    )
    message = public_failure_message("pipeline_failed", stored)
    assert "Errno" not in message
    assert "Connection refused" not in message
    assert "chunks failed" not in message
    assert message == "文档知识入库失败，请稍后重试。"


def test_public_failure_message_keeps_extract_guidance():
    assert (
        public_failure_message("extract_failed", "无法从 pdf 文件中提取文本")
        == "无法从 pdf 文件中提取文本"
    )
