#!/usr/bin/env python3
"""Audit evidence caches against PostgreSQL metadata or an offline SQLite copy.

Default: read-only JSONL report. --apply freezes current text; with --reextract
it retains old/new text before refreshing sidecars. Only --fetch-source allows
MinIO reads into bounded temporary files. Originals and historical citation
bindings are never modified. Configured PostgreSQL --apply --reextract updates
only the matching version's extraction summary; old page indexes stay untouched.
New snapshots cannot verify legacy IDs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

sys.dont_write_bytecode = True


def _inside(path: Path, root: Path) -> Path | None:
    resolved = path.resolve()
    return resolved if resolved.is_relative_to(root) else None


def _snapshot_report(version_id: str, upload_dir: Path) -> list[dict[str, str]]:
    from app.services.evidence_snapshots import parse_citation_id, read_snapshot

    root = upload_dir / "_evidence_snapshots"
    directory = root / version_id
    if root.is_symlink() or directory.is_symlink() or not directory.is_dir():
        return []
    result = []
    for path in sorted(directory.iterdir()):
        digest = path.stem
        if path.suffix != ".txt" or parse_citation_id(f"writing-evidence:{version_id}:{digest}:0-1") is None:
            continue
        result.append({
            "text_sha256": digest,
            "state": "verified" if read_snapshot(version_id, digest) is not None else "unavailable",
        })
    return result


class _SourceReadError(Exception):
    pass


def _extract_pdf_source(
    file_path: str,
    source: Path | None,
    *,
    fetch_source: bool,
    max_source_bytes: int,
):
    from app.core.config import settings
    from app.services import minio_storage
    from app.services.pdf_ocr import extract_pdf_pages

    if source is not None:
        if source.suffix.lower() != ".pdf":
            raise _SourceReadError("pdf_required")
        with source.open("rb") as stream:
            payload = stream.read(max_source_bytes + 1)
        if len(payload) > max_source_bytes:
            raise _SourceReadError("source_too_large")
        return extract_pdf_pages(payload, vision_enabled=False)
    if not file_path.startswith("minio://"):
        raise _SourceReadError("source_unavailable")
    if not fetch_source:
        raise _SourceReadError("source_fetch_not_authorized")
    bucket, key = minio_storage.parse_minio_uri(file_path)
    if Path(key).suffix.lower() != ".pdf":
        raise _SourceReadError("pdf_required")
    # Use the storage module's client, but never its legacy DB config fallback:
    # that fallback opens the application's writable/WAL-enabled connection.
    if (
        settings.minio_enabled.lower() in {"0", "false", "no", "off"}
        or not settings.minio_url
        or not settings.minio_access_key
        or not settings.minio_secret_key
    ):
        raise _SourceReadError("minio_environment_not_configured")
    config = minio_storage.MinioSettings(
        endpoint_url=settings.minio_url,
        show_url=settings.minio_show_url,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        bucket=settings.minio_bucket,
        temp_bucket=settings.minio_temp_bucket,
        presign_minutes=settings.minio_presign_minutes,
    )
    client = minio_storage._client(config)
    metadata = client.stat_object(bucket, key)
    if metadata.size is None or metadata.size < 0 or metadata.size > max_source_bytes:
        raise _SourceReadError("source_too_large")
    with tempfile.TemporaryDirectory(prefix="evidence-source-") as directory:
        target = Path(directory) / "source.pdf"
        response = client.get_object(bucket, key)
        try:
            received = 0
            with target.open("wb") as stream:
                while True:
                    chunk = response.read(min(4 * 1024 * 1024, max_source_bytes - received + 1))
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > max_source_bytes:
                        raise _SourceReadError("source_too_large")
                    stream.write(chunk)
        finally:
            try:
                response.close()
            finally:
                response.release_conn()
        if received != metadata.size:
            raise _SourceReadError("source_changed_during_fetch")
        return extract_pdf_pages(target.read_bytes(), vision_enabled=False)


@contextmanager
def _version_rows(
    database_path: Path | None,
    configured_url: str,
    version_ids: list[str],
) -> Iterator[tuple[Iterator[Mapping[str, Any]], Any]]:
    query = "SELECT id, file_path, processing_stage, queryable FROM document_versions"
    if database_path is not None:
        # immutable avoids creating/deleting WAL or SHM files on the host.
        with closing(sqlite3.connect(database_path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            db.row_factory = sqlite3.Row
            if version_ids:
                query += " WHERE id IN (" + ",".join("?" for _ in version_ids) + ")"
            cursor = db.execute(query + " ORDER BY id", tuple(version_ids))
            yield (dict(row) for row in cursor), None
        return

    from sqlalchemy import bindparam, create_engine, text
    query = (
        "SELECT id, file_path, processing_stage, queryable, updated_at, extraction_method, "
        "page_count, ocr_page_count, vision_page_count, extraction_warning_code FROM document_versions"
    )

    engine = create_engine(configured_url, future=True, echo=False, hide_parameters=True)
    try:
        with engine.connect() as connection:
            with connection.begin() as transaction:
                # No application engine, migration hooks, or committing context.
                connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                if version_ids:
                    query += " WHERE id IN :version_ids"
                statement = text(query + " ORDER BY id")
                parameters: dict[str, Any] = {}
                if version_ids:
                    statement = statement.bindparams(bindparam("version_ids", expanding=True))
                    parameters["version_ids"] = version_ids
                result = connection.execution_options(yield_per=200).execute(statement, parameters)
                try:
                    yield iter(result.mappings()), engine
                finally:
                    result.close()
                    transaction.rollback()
    finally:
        engine.dispose()


def _refresh_with_metadata(
    engine: Any,
    row: Mapping[str, Any],
    file_path: str,
    extraction: Any,
) -> str:
    """Update one matching version; never alter old page rows or citation refs."""
    from sqlalchemy import text
    from app.core.security import now
    from app.services.document_processor import write_extracted_sidecar

    with engine.begin() as connection:
        current = connection.execute(
            text("SELECT file_path, updated_at FROM document_versions WHERE id=:id FOR UPDATE"),
            {"id": row["id"]},
        ).mappings().first()
        if current is None or current["file_path"] != row["file_path"] or current["updated_at"] != row["updated_at"]:
            return "version_changed"
        if write_extracted_sidecar(file_path, extraction.text, version_id=str(row["id"])) is None:
            return "preservation_failed"
        connection.execute(
            text(
                "UPDATE document_versions SET extraction_method=:method, page_count=:pages, "
                "ocr_page_count=:ocr_pages, vision_page_count=:vision_pages, "
                "extraction_warning_code=:warning, vision_usage_json=:vision_usage, updated_at=:updated "
                "WHERE id=:id"
            ),
            {
                "id": row["id"],
                "method": extraction.extraction_method,
                "pages": len(extraction.pages),
                "ocr_pages": extraction.ocr_page_count,
                "vision_pages": extraction.vision_page_count,
                "warning": extraction.warning_code,
                "vision_usage": "{}",
                "updated": now(),
            },
        )
    return "refreshed"


def _audit_version(
    row: Mapping[str, Any],
    *,
    upload_dir: Path,
    source_root: Path,
    apply: bool,
    reextract: bool,
    fetch_source: bool = False,
    max_source_bytes: int = 100 * 1024 * 1024,
    metadata_engine: Any = None,
) -> dict[str, Any]:
    from app.services.document_processor import (
        decode_text_bytes,
        extracted_sidecar_path,
        write_extracted_sidecar,
    )
    from app.services.evidence_snapshots import freeze_text, make_citation_id

    version_id = str(row["id"])
    report: dict[str, Any] = {
        "version_id": version_id,
        "processing_stage": row["processing_stage"],
        "queryable": bool(row["queryable"]),
        "legacy_binding_state": "unverified",
        "applied": False,
    }
    if "extraction_method" in row:
        report["stored_extraction"] = {
            "method": row["extraction_method"],
            "page_count": row["page_count"],
            "ocr_page_count": row["ocr_page_count"],
            "vision_page_count": row["vision_page_count"],
            "warning_code": row["extraction_warning_code"],
        }
    try:
        make_citation_id(version_id, "0" * 64, 0, 1)
    except ValueError:
        return {**report, "read_state": "invalid_version_id", "path": None, "snapshots": []}

    file_path = str(row["file_path"] or "")
    source: Path | None = None
    if file_path.startswith("minio://"):
        # Object locations are redacted; fetching needs a separate explicit flag.
        report["path"] = "minio:sha256:" + hashlib.sha256(file_path.encode("utf-8")).hexdigest()
        sidecar = extracted_sidecar_path(file_path)
        allowed_root = upload_dir
    elif file_path and "://" not in file_path:
        source = _inside(Path(file_path), source_root)
        report["path"] = str(source.relative_to(source_root)) if source else "outside-source-root"
        sidecar = extracted_sidecar_path(str(source)) if source else None
        allowed_root = source_root
    else:
        report["path"] = None if not file_path else "unsupported-uri"
        sidecar = None
        allowed_root = source_root

    if sidecar is not None and (sidecar.is_symlink() or _inside(sidecar, allowed_root) is None):
        sidecar = None
    report["sidecar_path"] = str(sidecar.relative_to(allowed_root)) if sidecar else None
    current: str | None = None
    if sidecar is None:
        report["read_state"] = "unavailable_path"
    else:
        try:
            current = sidecar.read_bytes().decode("utf-8")
            report["read_state"] = "readable" if current.strip() else "empty"
        except FileNotFoundError:
            report["read_state"] = "missing_sidecar"
        except UnicodeError:
            report["read_state"] = "invalid_utf8"
        except OSError:
            report["read_state"] = "unreadable"

    # Plain originals need no extraction sidecar, and remain byte-for-byte intact.
    if current is None and source is not None and source.suffix.lower() in {".txt", ".md", ".markdown", ".csv"}:
        try:
            current = decode_text_bytes(source.read_bytes())
            report["read_state"] = "readable_original" if current.strip() else "empty"
        except (OSError, UnicodeError):
            report["read_state"] = "unreadable"
    report["current_text_sha256"] = hashlib.sha256(current.encode("utf-8")).hexdigest() if current is not None else None

    refreshed: str | None = None
    extracted = None
    if reextract:
        if sidecar is None:
            report["refresh_state"] = "source_unavailable"
        else:
            try:
                extracted = _extract_pdf_source(
                    file_path, source, fetch_source=fetch_source,
                    max_source_bytes=max_source_bytes,
                )
                refreshed = extracted.text
                report["refresh_state"] = "ready" if refreshed.strip() else "empty"
                report["refresh_warning_code"] = extracted.warning_code
                report["refreshed_text_sha256"] = hashlib.sha256(refreshed.encode("utf-8")).hexdigest()
                report["extraction"] = {
                    "method": extracted.extraction_method,
                    "page_count": len(extracted.pages),
                    "ocr_page_count": extracted.ocr_page_count,
                    "vision_page_count": extracted.vision_page_count,
                    "warning_code": extracted.warning_code,
                }
            except _SourceReadError as exc:
                report["refresh_state"] = str(exc)
            except Exception:
                # Exception strings may carry document text, credentials, or paths.
                report["refresh_state"] = "extraction_failed"

    if apply:
        try:
            if current is not None:
                freeze_text(version_id, current)
                report["applied"] = True
            if refreshed is not None and refreshed.strip() and extracted is not None:
                source_path = str(source) if source is not None else file_path
                if metadata_engine is not None:
                    from sqlalchemy.exc import SQLAlchemyError

                    try:
                        report["refresh_state"] = _refresh_with_metadata(
                            metadata_engine, row, source_path, extracted,
                        )
                    except SQLAlchemyError:
                        # The filesystem and database cannot share a transaction.
                        # Exact snapshots survive; explicitly report metadata failure.
                        report["refresh_state"] = "metadata_repair_failed"
                    report["extraction_metadata_updated"] = report["refresh_state"] == "refreshed"
                else:
                    target = write_extracted_sidecar(source_path, refreshed, version_id=version_id)
                    report["refresh_state"] = "refreshed" if target is not None else "preservation_failed"
                    report["extraction_metadata_updated"] = False
                report["page_index_state"] = "unchanged_requires_text_binding"
                report["applied"] = report["applied"] or report["refresh_state"] == "refreshed"
        except (OSError, ValueError, UnicodeError):
            report["apply_state"] = "snapshot_unavailable"
    report["snapshots"] = _snapshot_report(version_id, upload_dir)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    database_group = parser.add_mutually_exclusive_group(required=True)
    database_group.add_argument("--database-copy", help="Offline SQLite backup/copy, never a running service database")
    database_group.add_argument("--configured-database", action="store_true", help="Read configured PostgreSQL metadata in a read-only transaction")
    parser.add_argument("--upload-dir", help="Local upload/cache root (defaults to configured upload_dir)")
    parser.add_argument("--source-root", help="Allowed local originals root (defaults to upload-dir)")
    parser.add_argument("--version-id", action="append", default=[], help="Audit only these versions (repeatable)")
    parser.add_argument("--apply", action="store_true", help="Freeze current text; with --reextract refresh caches after preservation")
    parser.add_argument("--reextract", action="store_true", help="Preview existing local PDF/OCR extractor, with network vision disabled")
    parser.add_argument("--fetch-source", action="store_true", help="Explicitly allow MinIO PDF downloads to bounded temporary files; requires --reextract and environment MinIO settings")
    parser.add_argument("--max-source-mb", type=int, help="Maximum source size, no larger than configured document_upload_max_mb")
    parser.add_argument("--isolated-uploads", action="store_true", help="Confirm --database-copy uploads are an isolated offline copy; required to apply re-extraction in offline mode")
    args = parser.parse_args(argv)

    if args.fetch_source and not args.reextract:
        parser.error("--fetch-source requires --reextract")
    if args.database_copy and args.apply and args.reextract and not args.isolated_uploads:
        parser.error("Offline re-extraction apply requires --isolated-uploads; never refresh live service caches against a copied database")
    for raw in (args.database_copy, args.upload_dir, args.source_root):
        if raw and ("://" in raw or raw.startswith("//")):
            parser.error("Filesystem arguments must be local paths")
    database_path = Path(args.database_copy).expanduser().resolve() if args.database_copy else None
    if database_path is not None:
        if not database_path.is_file():
            parser.error("The offline SQLite database copy must already exist")
        if any(Path(str(database_path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            parser.error("Use an offline consistent SQLite backup, not a database with live journal files")

    backend = Path(__file__).resolve().parents[1]
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from app.core.config import settings
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import SQLAlchemyError

    if args.configured_database:
        try:
            backend_name = make_url(settings.database_url).get_backend_name()
        except SQLAlchemyError:
            parser.error("Configured database URL is invalid")
        if backend_name != "postgresql":
            parser.error("--configured-database accepts PostgreSQL only; SQLite requires --database-copy (never open a live host SQLite database)")
    upload_dir = Path(args.upload_dir).expanduser().resolve() if args.upload_dir else settings.upload_dir.resolve()
    source_root = Path(args.source_root).expanduser().resolve() if args.source_root else upload_dir
    if not upload_dir.is_dir() or not source_root.is_dir():
        parser.error("Local upload and source directories must already exist")
    max_source_mb = args.max_source_mb if args.max_source_mb is not None else settings.document_upload_max_mb
    if not 1 <= max_source_mb <= settings.document_upload_max_mb:
        parser.error("--max-source-mb must be positive and no larger than document_upload_max_mb")

    previous_upload_dir = settings.upload_dir
    settings.upload_dir = upload_dir
    try:
        with _version_rows(database_path, settings.database_url, args.version_id) as (rows, metadata_engine):
            count = 0
            for row in rows:
                report = _audit_version(
                    row, upload_dir=upload_dir, source_root=source_root,
                    apply=args.apply, reextract=args.reextract,
                    fetch_source=args.fetch_source, max_source_bytes=max_source_mb * 1024 * 1024,
                    metadata_engine=metadata_engine,
                )
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
                count += 1
            print(json.dumps({
                "versions": count, "read_only": not args.apply,
                "database_read_only": not (args.configured_database and args.apply and args.reextract),
                "source_fetch_enabled": args.fetch_source,
                "legacy_bindings_modified": False,
            }))
    except (sqlite3.Error, SQLAlchemyError, OSError):
        print(json.dumps({"error": "local_evidence_audit_failed"}), file=sys.stderr)
        return 1
    finally:
        settings.upload_dir = previous_upload_dir
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
