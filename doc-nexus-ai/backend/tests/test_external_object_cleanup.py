from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from app.core import database
from app.core.config import settings
from app.core.security import as_utc, now
from app.models.auth import User
from app.models.chat import ChatMessage, ChatSession
from app.models.external_object_cleanup import ExternalObjectCleanupOutbox
from app.models.image_generation import ImageGenerationTask
from app.models.video_generation import VideoGenerationTask
from app.repositories import chat as chat_repository
from app.repositories import external_object_cleanup as cleanup_repository
from app.repositories import users as user_repository
from app.services import external_object_cleanup, image_edit_source, image_generation
from app.services.minio_storage import MinioSettings


IMAGE_URI = "minio://dgmeta/image-generation/results/usr_cleanup/igt_cleanup"


def _configure_minio(monkeypatch: pytest.MonkeyPatch) -> MinioSettings:
    config = MinioSettings(
        endpoint_url="http://minio:9000",
        show_url="http://minio:9000",
        access_key="access",
        secret_key="secret",
        bucket="dgmeta",
        temp_bucket="dgmeta-temp",
        presign_minutes=120,
    )
    monkeypatch.setattr(
        external_object_cleanup.minio_storage,
        "load_minio_settings",
        lambda: config,
    )
    return config


@contextmanager
def _isolated_db(tmp_path: Path):
    old_path, old_url = settings.db_path, settings.database_url
    db_path = tmp_path / "external-object-cleanup.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    try:
        database.initialize_database()
        yield
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(db_path)


def _seed_media_rows(db) -> tuple[User, ChatSession]:
    stamp = now()
    user = User(
        id="usr_cleanup",
        username="cleanup-user",
        email="cleanup@example.com",
        display_name="Cleanup User",
        password_hash="hashed",
        role="user",
        created_at=stamp,
    )
    session = ChatSession(
        id="chs_cleanup",
        owner_user_id=user.id,
        title="cleanup",
        created_at=stamp,
        updated_at=stamp,
    )
    user_message = ChatMessage(
        id="chm_cleanup_user",
        session_id=session.id,
        owner_user_id=user.id,
        role="user",
        content="hello",
        images_json=json.dumps(
            [{"data": "", "mimeType": "", "url": IMAGE_URI}]
        ),
        created_at=stamp,
    )
    assistant_message = ChatMessage(
        id="chm_cleanup_assistant",
        session_id=session.id,
        owner_user_id=user.id,
        role="assistant",
        content="done",
        created_at=stamp,
    )
    image_task = ImageGenerationTask(
        id="igt_cleanup",
        owner_user_id=user.id,
        chat_session_id=session.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        model="image-model",
        aspect_ratio="1:1",
        resolution="1k",
        prompt="hello",
        status="succeeded",
        image_cache_uri=IMAGE_URI,
        created_at=stamp,
        updated_at=stamp,
    )
    video_task = VideoGenerationTask(
        id="vgt_cleanup",
        owner_user_id=user.id,
        chat_session_id=session.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        model="video-model",
        mode="std",
        duration=3,
        prompt="hello",
        status="succeeded",
        source_file_ref="minio://dgmeta-temp/video-generation/first-frames/cleanup.png",
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(user)
    db.flush()
    db.add(session)
    db.flush()
    db.add_all([user_message, assistant_message])
    db.flush()
    db.add_all([image_task, video_task])
    db.commit()
    return user, session


def test_session_delete_enqueues_media_before_cascade_and_rollback_keeps_tracking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    with _isolated_db(tmp_path):
        _configure_minio(monkeypatch)
        with database.SessionLocal() as db:
            _, session = _seed_media_rows(db)

        with pytest.raises(RuntimeError, match="rollback"):
            with database.transaction() as db:
                target = db.get(ChatSession, session.id)
                assert target is not None
                chat_repository.delete_session(db, target)
                uris = set(
                    db.scalars(select(ExternalObjectCleanupOutbox.uri)).all()
                )
                assert uris == {IMAGE_URI}
                raise RuntimeError("rollback")

        with database.SessionLocal() as db:
            assert db.get(ChatSession, session.id) is not None
            assert db.get(ImageGenerationTask, "igt_cleanup") is not None
            assert db.scalar(select(ExternalObjectCleanupOutbox.uri)) is None

        with database.transaction() as db:
            target = db.get(ChatSession, session.id)
            assert target is not None
            chat_repository.delete_session(db, target)

        with database.SessionLocal() as db:
            assert db.get(ChatSession, session.id) is None
            assert {
                row.uri
                for row in db.scalars(select(ExternalObjectCleanupOutbox)).all()
            } == {IMAGE_URI}


def test_hard_user_purge_enqueues_owned_media_before_cascade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    with _isolated_db(tmp_path):
        _configure_minio(monkeypatch)
        with database.SessionLocal() as db:
            user, _ = _seed_media_rows(db)
            admin = User(
                id="usr_admin",
                username="cleanup-admin",
                email="cleanup-admin@example.com",
                display_name="Cleanup Admin",
                password_hash="hashed",
                role="admin",
                created_at=now(),
            )
            db.add(admin)
            db.commit()

        with database.transaction() as db:
            target = db.get(User, user.id)
            assert target is not None
            user_repository.hard_delete(
                db, target, reassign_to_user_id="usr_admin"
            )

        with database.SessionLocal() as db:
            assert db.get(User, user.id) is None
            assert db.get(ImageGenerationTask, "igt_cleanup") is None
            assert db.scalar(
                select(ExternalObjectCleanupOutbox).where(
                    ExternalObjectCleanupOutbox.uri
                    == IMAGE_URI
                )
            ) is not None


def test_enqueue_is_idempotent_by_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    with _isolated_db(tmp_path):
        _configure_minio(monkeypatch)
        with database.transaction() as db:
            first = external_object_cleanup.enqueue(
                db,
                "minio://dgmeta/image-generation/results/usr_cleanup/igt_duplicate",
                resource_kind="image_cache",
            )
            second = external_object_cleanup.enqueue(
                db,
                "minio://dgmeta/image-generation/results/usr_cleanup/igt_duplicate",
                resource_kind="image_cache",
            )
            assert first.id == second.id
            assert first.resource_kind == "image_cache"

        with database.SessionLocal() as db:
            assert (
                db.scalar(
                    select(ExternalObjectCleanupOutbox.id).where(
                        ExternalObjectCleanupOutbox.uri
                        == "minio://dgmeta/image-generation/results/usr_cleanup/igt_duplicate"
                    )
                )
                == first.id
            )
            assert db.query(ExternalObjectCleanupOutbox).count() == 1


@pytest.mark.parametrize(
    ("uri", "resource_kind"),
    [
        ("minio://other-bucket/image-generation/results/usr_cleanup/igt_bad", "image_cache"),
        ("minio://dgmeta/document/space/file.pdf", "image_cache"),
        ("minio://dgmeta/upload/session/file.png", "image_cache"),
        ("minio://dgmeta-temp/image-generation/results/usr_cleanup/igt_bad", "image_cache"),
        ("minio://dgmeta/image-generation/results/usr_cleanup/igt_bad/extra", "image_cache"),
        ("https://dgmeta/image-generation/results/usr_cleanup/igt_bad", "image_cache"),
        (IMAGE_URI, "document"),
    ],
)
def test_enqueue_rejects_untrusted_uri_or_resource_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    uri: str,
    resource_kind: str,
):
    with _isolated_db(tmp_path):
        _configure_minio(monkeypatch)
        with database.transaction() as db:
            assert (
                external_object_cleanup.enqueue(
                    db, uri, resource_kind=resource_kind
                )
                is None
            )

        with database.SessionLocal() as db:
            assert db.scalar(select(ExternalObjectCleanupOutbox.uri)) is None


@pytest.mark.asyncio
async def test_worker_does_not_delete_historical_untrusted_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    dirty_uri = "minio://other-bucket/image-generation/results/usr_cleanup/igt_dirty"
    with _isolated_db(tmp_path):
        _configure_minio(monkeypatch)
        with database.transaction() as db:
            item = cleanup_repository.enqueue(
                db,
                dirty_uri,
                resource_kind=external_object_cleanup.RESOURCE_KIND_IMAGE_CACHE,
            )

        deleted: list[str] = []
        monkeypatch.setattr(
            image_generation,
            "delete_cached_image",
            lambda uri: deleted.append(uri) or True,
        )

        assert await external_object_cleanup.process_pending_once() is True
        assert deleted == []
        with database.SessionLocal() as db:
            stored = db.get(ExternalObjectCleanupOutbox, item.id)
            assert stored is not None
            assert stored.status == "pending"
            assert "unsafe" in (stored.last_error or "")


@pytest.mark.asyncio
async def test_worker_defers_shared_uri_until_last_reference_and_enqueue_wakes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    with _isolated_db(tmp_path):
        _configure_minio(monkeypatch)
        with database.SessionLocal() as db:
            _, session = _seed_media_rows(db)
            message = db.get(ChatMessage, "chm_cleanup_user")
            assert message is not None
            message.images_json = "[]"
            db.commit()

        with database.transaction() as db:
            first = db.get(ImageGenerationTask, "igt_cleanup")
            assert first is not None
            db.add(
                ImageGenerationTask(
                    id="igt_cleanup_2",
                    owner_user_id=first.owner_user_id,
                    chat_session_id=first.chat_session_id,
                    user_message_id=first.user_message_id,
                    assistant_message_id=first.assistant_message_id,
                    model=first.model,
                    aspect_ratio=first.aspect_ratio,
                    resolution=first.resolution,
                    prompt=first.prompt,
                    status="succeeded",
                    image_cache_uri=IMAGE_URI,
                    created_at=now(),
                    updated_at=now(),
                )
            )

        deleted: list[str] = []
        monkeypatch.setattr(
            image_generation,
            "delete_cached_image",
            lambda uri: deleted.append(uri) or True,
        )
        with database.transaction() as db:
            item = external_object_cleanup.enqueue(
                db,
                IMAGE_URI,
                resource_kind=external_object_cleanup.RESOURCE_KIND_IMAGE_CACHE,
            )

        assert await external_object_cleanup.process_pending_once() is True
        with database.SessionLocal() as db:
            deferred = db.get(ExternalObjectCleanupOutbox, item.id)
            assert deferred is not None
            assert deferred.status == "pending"
            assert "referenced" in (deferred.last_error or "")
        assert deleted == []

        with database.transaction() as db:
            db.delete(db.get(ImageGenerationTask, "igt_cleanup"))
            external_object_cleanup.enqueue(
                db,
                IMAGE_URI,
                resource_kind=external_object_cleanup.RESOURCE_KIND_IMAGE_CACHE,
            )

        assert await external_object_cleanup.process_pending_once() is True
        with database.SessionLocal() as db:
            still_deferred = db.get(ExternalObjectCleanupOutbox, item.id)
            assert still_deferred is not None
            assert still_deferred.status == "pending"
        assert deleted == []

        with database.transaction() as db:
            db.delete(db.get(ImageGenerationTask, "igt_cleanup_2"))
            external_object_cleanup.enqueue(
                db,
                IMAGE_URI,
                resource_kind=external_object_cleanup.RESOURCE_KIND_IMAGE_CACHE,
            )

        assert await external_object_cleanup.process_pending_once() is True
        assert deleted == [IMAGE_URI]
        with database.SessionLocal() as db:
            completed = db.get(ExternalObjectCleanupOutbox, item.id)
            assert completed is not None
            assert completed.status == "succeeded"


def test_sqlite_claim_has_one_winner_for_concurrent_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    with _isolated_db(tmp_path):
        with database.transaction() as db:
            cleanup_repository.enqueue(
                db,
                "minio://dgmeta/image-generation/results/usr_cleanup/igt_claim",
                resource_kind=external_object_cleanup.RESOURCE_KIND_IMAGE_CACHE,
            )

        barrier = threading.Barrier(2)
        original_new_token = cleanup_repository.new_token

        def synchronized_token(length: int = 32) -> str:
            if length == 24:
                barrier.wait(timeout=5)
            return original_new_token(length)

        monkeypatch.setattr(cleanup_repository, "new_token", synchronized_token)

        def claim() -> str | None:
            with database.transaction() as db:
                item = cleanup_repository.claim_next(db)
                return item.claim_token if item is not None else None

        with ThreadPoolExecutor(max_workers=2) as executor:
            tokens = list(executor.map(lambda _: claim(), range(2)))

        assert [token for token in tokens if token] and sum(
            token is not None for token in tokens
        ) == 1


def test_delete_cached_image_treats_missing_object_as_success(monkeypatch):
    class NoSuchKeyError(Exception):
        code = "NoSuchKey"

    def missing_delete(*, minio_uri: str):
        raise NoSuchKeyError(minio_uri)

    monkeypatch.setattr(image_generation.minio_storage, "delete_object", missing_delete)

    assert image_generation.delete_cached_image("minio://dgmeta/image-cache/missing")


@pytest.mark.asyncio
async def test_cleanup_loop_keeps_worker_separate_from_media_reconciliation(
    monkeypatch,
):
    from app.services.background_workers import BackgroundWorkerService

    scans: list[bool] = []
    worker = BackgroundWorkerService("background")

    async def fake_process_pending_once(**_kwargs):
        scans.append(True)
        return False

    async def stop_after_sleep(_interval: float):
        raise asyncio.CancelledError

    monkeypatch.setattr(
        external_object_cleanup,
        "process_pending_once",
        fake_process_pending_once,
    )
    monkeypatch.setattr(worker, "_sleep", stop_after_sleep)
    try:
        with pytest.raises(asyncio.CancelledError):
            await worker._external_object_cleanup_loop()
    finally:
        worker.resources.close()

    assert scans == [True]


@pytest.mark.asyncio
async def test_cleanup_retries_failure_then_succeeds_and_uses_thread(tmp_path, monkeypatch):
    with _isolated_db(tmp_path):
        _configure_minio(monkeypatch)
        uri = "minio://dgmeta/image-generation/results/usr_cleanup/igt_retry"
        with database.transaction() as db:
            external_object_cleanup.enqueue(
                db, uri, resource_kind=external_object_cleanup.RESOURCE_KIND_MINIO
            )

        attempts: list[str] = []
        failures = [RuntimeError("temporary"), None]

        def fake_delete(*, minio_uri: str):
            attempts.append(minio_uri)
            failure = failures.pop(0)
            if failure:
                raise failure

        to_thread_calls: list[object] = []
        original_to_thread = asyncio.to_thread

        async def recording_to_thread(function, *args, **kwargs):
            to_thread_calls.append(function)
            return await original_to_thread(function, *args, **kwargs)

        monkeypatch.setattr(external_object_cleanup.minio_storage, "delete_object", fake_delete)
        monkeypatch.setattr(external_object_cleanup.asyncio, "to_thread", recording_to_thread)
        monkeypatch.setattr(external_object_cleanup, "RETRY_BASE_SECONDS", 0)

        assert await external_object_cleanup.process_pending_once() is True
        with database.SessionLocal() as db:
            failed = db.scalar(
                select(ExternalObjectCleanupOutbox).where(
                    ExternalObjectCleanupOutbox.uri == uri
                )
            )
            assert failed is not None
            assert failed.status == "pending"
            assert failed.attempt_count == 1
            assert failed.last_error == "temporary"

        assert await external_object_cleanup.process_pending_once() is True
        with database.SessionLocal() as db:
            succeeded = db.scalar(
                select(ExternalObjectCleanupOutbox).where(
                    ExternalObjectCleanupOutbox.uri == uri
                )
            )
            assert succeeded is not None
            assert succeeded.status == "succeeded"
            assert succeeded.attempt_count == 2
            assert succeeded.last_error is None
        assert attempts == [uri, uri]
        assert to_thread_calls.count(external_object_cleanup._delete_external_object) == 2
        assert len(to_thread_calls) >= 6


@pytest.mark.asyncio
async def test_cleanup_treats_missing_object_as_success_and_reclaims_stale_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    with _isolated_db(tmp_path):
        _configure_minio(monkeypatch)
        uri = "minio://dgmeta/image-generation/results/usr_cleanup/igt_missing"
        with database.transaction() as db:
            item = external_object_cleanup.enqueue(
                db, uri, resource_kind=external_object_cleanup.RESOURCE_KIND_IMAGE_CACHE
            )
            claimed = external_object_cleanup.cleanup_repository.claim_next(
                db, lease_seconds=1
            )
            assert claimed is not None
            claimed.claimed_at = now() - timedelta(seconds=10)

        class NoSuchKeyError(Exception):
            code = "NoSuchKey"

        def missing_delete(*, minio_uri: str):
            raise NoSuchKeyError(minio_uri)

        from app.services import minio_storage

        original = minio_storage.delete_object
        minio_storage.delete_object = missing_delete
        try:
            assert await external_object_cleanup.process_pending_once(lease_seconds=1)
        finally:
            minio_storage.delete_object = original

        with database.SessionLocal() as db:
            stored = db.get(ExternalObjectCleanupOutbox, item.id)
            assert stored is not None
            assert stored.status == "succeeded"


@pytest.fixture
def edit_source_storage(monkeypatch):
    config = replace(
        _configure_minio(monkeypatch),
        show_url="https://images.example.com:9443",
    )
    monkeypatch.setattr(
        image_edit_source.minio_storage, "load_minio_settings", lambda: config
    )
    event_loop_thread = threading.get_ident()
    objects: dict[str, bytes] = {}

    class PrivateStorage:
        upload_error: BaseException | None = None

        def _get_region(self, bucket):
            assert threading.get_ident() != event_loop_thread
            assert bucket == config.bucket
            return "source-region-1"

        def put_object(self, bucket, key, data, *, length, content_type):
            assert threading.get_ident() != event_loop_thread
            uri = image_edit_source.minio_storage.build_minio_uri(bucket, key)
            # A separate connection must see the tombstone before any bytes arrive.
            with database.SessionLocal() as db:
                row = db.scalar(
                    select(ExternalObjectCleanupOutbox).where(
                        ExternalObjectCleanupOutbox.uri == uri
                    )
                )
                assert row is not None
                assert row.status == "pending"
                assert as_utc(row.next_attempt_at) > now() + timedelta(hours=24)
            content = data.read()
            assert len(content) == length
            assert content_type == "image/png"
            objects[uri] = content
            if self.upload_error is not None:
                raise self.upload_error

        def remove_object(self, bucket, key):
            objects.pop(image_edit_source.minio_storage.build_minio_uri(bucket, key))

    client = PrivateStorage()
    monkeypatch.setattr(image_edit_source.minio_storage, "_client", lambda cfg: client)

    def public_dns(host, port, *, type):
        assert threading.get_ident() != event_loop_thread
        return [(socket.AF_INET, type, 6, "", ("8.8.8.8", port))]

    monkeypatch.setattr(image_edit_source.socket, "getaddrinfo", public_dns)
    return config, objects, client


@pytest.mark.asyncio
async def test_edit_sources_sign_public_host_and_expire_independently(
    tmp_path, monkeypatch, edit_source_storage
):
    config, objects, _client = edit_source_storage
    with _isolated_db(tmp_path):
        stamp = now()
        monkeypatch.setattr(image_edit_source, "now", lambda: stamp)
        first_url = await image_edit_source.publish_source(
            content=b"first-private-image", mime_type="image/png"
        )
        parsed = urlsplit(first_url)
        query = parse_qs(parsed.query)
        assert parsed.scheme == "https"
        assert parsed.netloc == "images.example.com:9443"
        assert query["X-Amz-Expires"] == ["86400"]
        assert query["X-Amz-SignedHeaders"] == ["host"]
        credential = query["X-Amz-Credential"][0]
        assert credential.endswith("/source-region-1/s3/aws4_request")
        assert parsed.path.startswith("/dgmeta/image-generation/results/edit-source/")

        # Independently verify SigV4 against the public authority, not a rewritten URL.
        canonical_query = "&".join(
            part for part in parsed.query.split("&")
            if not part.startswith("X-Amz-Signature=")
        )
        canonical_request = "\n".join(
            ["GET", parsed.path, canonical_query, f"host:{parsed.netloc}\n", "host", "UNSIGNED-PAYLOAD"]
        )
        scope = credential.split("/", 1)[1]
        string_to_sign = "\n".join(
            ["AWS4-HMAC-SHA256", query["X-Amz-Date"][0], scope,
             hashlib.sha256(canonical_request.encode()).hexdigest()]
        )
        signing_key = f"AWS4{config.secret_key}".encode()
        for part in scope.split("/"):
            signing_key = hmac.new(signing_key, part.encode(), hashlib.sha256).digest()
        assert hmac.new(
            signing_key, string_to_sign.encode(), hashlib.sha256
        ).hexdigest() == query["X-Amz-Signature"][0]

        first_uri = next(iter(objects))
        stamp += timedelta(hours=1)
        await image_edit_source.publish_source(
            content=b"second-private-image", mime_type="image/png"
        )
        second_uri = next(uri for uri in objects if uri != first_uri)
        assert objects == {
            first_uri: b"first-private-image",
            second_uri: b"second-private-image",
        }
        with database.SessionLocal() as db:
            due = {
                row.uri: as_utc(row.next_attempt_at)
                for row in db.scalars(select(ExternalObjectCleanupOutbox))
            }
        assert due[first_uri] == stamp + timedelta(hours=24)
        assert due[second_uri] == stamp + timedelta(hours=25)
        assert not await external_object_cleanup.process_pending_once(
            now_at=due[first_uri] - timedelta(seconds=1)
        )
        assert await external_object_cleanup.process_pending_once(now_at=due[first_uri])
        assert objects == {second_uri: b"second-private-image"}
        assert not await external_object_cleanup.process_pending_once(now_at=due[first_uri])
        assert await external_object_cleanup.process_pending_once(now_at=due[second_uri])
        assert objects == {}


@pytest.mark.parametrize(
    ("show_url", "addresses"),
    [
        ("http://images.example.com", ["8.8.8.8"]),
        ("https://user:secret@images.example.com", ["8.8.8.8"]),
        ("https://images.example.com/path", ["8.8.8.8"]),
        ("https://images.example.com?token=secret", ["8.8.8.8"]),
        ("https://images.example.com#secret", ["8.8.8.8"]),
        ("https://localhost", ["8.8.8.8"]),
        ("https://minio", ["8.8.8.8"]),
        ("https://minio.local", ["8.8.8.8"]),
        ("https://127.0.0.1", ["127.0.0.1"]),
        ("https://192.168.0.1", ["192.168.0.1"]),
        ("https://100.64.0.1", ["100.64.0.1"]),
        ("https://224.0.0.1", ["224.0.0.1"]),
        ("https://[::1]", ["::1"]),
        ("https://images.example.com", ["8.8.8.8", "10.0.0.1"]),
        ("https://images.example.com", ["8.8.8.8", "2001:db8::1"]),
    ],
)
@pytest.mark.asyncio
async def test_edit_source_rejects_nonpublic_origin_before_upload(
    tmp_path, monkeypatch, edit_source_storage, show_url, addresses
):
    config, objects, _client = edit_source_storage
    monkeypatch.setattr(
        image_edit_source.minio_storage,
        "load_minio_settings",
        lambda: replace(config, show_url=show_url),
    )
    monkeypatch.setattr(
        image_edit_source.socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            for address in addresses
        ],
    )
    with _isolated_db(tmp_path):
        with pytest.raises(image_edit_source.ImageEditSourceNotConfiguredError) as raised:
            await image_edit_source.publish_source(content=b"private", mime_type="image/png")
        assert show_url not in str(raised.value)
        assert "secret" not in str(raised.value)
        assert objects == {}
        with database.SessionLocal() as db:
            assert db.scalar(select(ExternalObjectCleanupOutbox.id)) is None


@pytest.mark.asyncio
async def test_edit_source_dns_failure_is_safe_and_upload_requires_durable_cleanup(
    tmp_path, monkeypatch, edit_source_storage
):
    _config, objects, _client = edit_source_storage
    with _isolated_db(tmp_path):
        public_dns = image_edit_source.socket.getaddrinfo

        def unavailable_dns(*args, **kwargs):
            raise socket.gaierror("https://user:secret@private.internal")

        monkeypatch.setattr(image_edit_source.socket, "getaddrinfo", unavailable_dns)
        with pytest.raises(image_edit_source.ImageEditSourcePublishError) as raised:
            await image_edit_source.publish_source(content=b"private", mime_type="image/png")
        assert "secret" not in str(raised.value)
        assert objects == {}
        monkeypatch.setattr(image_edit_source.socket, "getaddrinfo", public_dns)

        @contextmanager
        def unavailable_database():
            raise RuntimeError("postgres://user:secret@database.internal")
            yield

        monkeypatch.setattr(image_edit_source, "transaction", unavailable_database)
        with pytest.raises(image_edit_source.ImageEditSourcePublishError) as raised:
            await image_edit_source.publish_source(content=b"private", mime_type="image/png")
        assert "secret" not in str(raised.value)
        assert objects == {}
        with database.SessionLocal() as db:
            assert db.scalar(select(ExternalObjectCleanupOutbox.id)) is None


class _SourcePublishInterrupted(BaseException):
    pass


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (RuntimeError("https://internal/object?secret=key"),
         image_edit_source.ImageEditSourcePublishError),
        (_SourcePublishInterrupted(), _SourcePublishInterrupted),
    ],
)
@pytest.mark.asyncio
async def test_edit_source_cleanup_survives_unknown_upload_outcome(
    tmp_path, edit_source_storage, failure, expected_error
):
    _config, objects, client = edit_source_storage
    client.upload_error = failure
    with _isolated_db(tmp_path):
        with pytest.raises(expected_error) as raised:
            await image_edit_source.publish_source(content=b"private", mime_type="image/png")
        assert "secret" not in str(raised.value)
        with database.SessionLocal() as db:
            row = db.scalar(select(ExternalObjectCleanupOutbox))
            assert row is not None
            assert objects == {row.uri: b"private"}
            due = as_utc(row.next_attempt_at)
        assert not await external_object_cleanup.process_pending_once(
            now_at=due - timedelta(seconds=1)
        )
        assert await external_object_cleanup.process_pending_once(now_at=due)
        assert objects == {}
