from app.services.minio_storage import (
    MinioSettings,
    _rewrite_host,
    build_minio_uri,
    ensure_configured_buckets,
    is_minio_uri,
    object_exists,
    object_key_for_version,
    parse_minio_uri,
)


def test_minio_uri_roundtrip():
    uri = build_minio_uri("dgmeta", "doc-nexus/a/b/c.pptx")
    assert is_minio_uri(uri)
    assert parse_minio_uri(uri) == ("dgmeta", "doc-nexus/a/b/c.pptx")


def test_object_key_for_version():
    key = object_key_for_version(
        space_id="spc_1",
        document_id="doc_1",
        version_id="ver_1",
        filename="可以.pptx",
    )
    assert key == "doc-nexus/spc_1/doc_1/ver_1.pptx"


def test_rewrite_presign_host_to_show_url():
    signed = "http://192.168.0.12:9000/dgmeta/doc-nexus/x.pptx?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=x"
    out = _rewrite_host(signed, "https://t-minio.digimeta.com.cn")
    assert out.startswith("https://t-minio.digimeta.com.cn/dgmeta/doc-nexus/x.pptx?")
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in out


def test_ensure_configured_buckets_creates_missing_only(monkeypatch):
    created: list[str] = []

    class FakeClient:
        def bucket_exists(self, name: str) -> bool:
            return name == "dgmeta"

        def make_bucket(self, name: str) -> None:
            created.append(name)

    monkeypatch.setattr("app.services.minio_storage._client", lambda cfg: FakeClient())
    cfg = MinioSettings(
        endpoint_url="http://minio:9000",
        show_url="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket="dgmeta",
        temp_bucket="dgmeta-temp",
        presign_minutes=120,
    )

    assert ensure_configured_buckets(cfg) == ["dgmeta-temp"]
    assert created == ["dgmeta-temp"]


def test_object_exists_stats_minio_uri(monkeypatch):
    calls: list[tuple[str, str]] = []

    class FakeClient:
        def stat_object(self, bucket: str, key: str) -> None:
            calls.append((bucket, key))

    cfg = MinioSettings(
        endpoint_url="http://minio:9000",
        show_url="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket="dgmeta",
        temp_bucket="dgmeta-temp",
        presign_minutes=120,
    )
    monkeypatch.setattr("app.services.minio_storage.load_minio_settings", lambda: cfg)
    monkeypatch.setattr("app.services.minio_storage._client", lambda _cfg: FakeClient())

    assert (
        object_exists(minio_uri="minio://dgmeta/image-generation/results/task") is True
    )
    assert calls == [("dgmeta", "image-generation/results/task")]


def test_object_exists_returns_false_for_missing_object(monkeypatch):
    class MissingObjectError(Exception):
        code = "NoSuchKey"

    class FakeClient:
        def stat_object(self, bucket: str, key: str) -> None:
            raise MissingObjectError

    cfg = MinioSettings(
        endpoint_url="http://minio:9000",
        show_url="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket="dgmeta",
        temp_bucket="dgmeta-temp",
        presign_minutes=120,
    )
    monkeypatch.setattr("app.services.minio_storage.load_minio_settings", lambda: cfg)
    monkeypatch.setattr("app.services.minio_storage._client", lambda _cfg: FakeClient())

    assert object_exists(object_key="missing.png") is False
