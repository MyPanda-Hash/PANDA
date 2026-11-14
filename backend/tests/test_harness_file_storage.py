from __future__ import annotations

from contextlib import nullcontext
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.harness import file_storage


def _docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("_rels/.rels", "<Relationships/>")
        archive.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()

def test_scan_uploads_workspace_docx_and_skips_internal_dirs(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "data" / "pi-runtime" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "慧云科技公司介绍.docx").write_bytes(_docx_bytes())
    session_log = workspace / "data" / "pi-runtime" / "sessions" / "session.jsonl"
    session_log.parent.mkdir(parents=True)
    session_log.write_text("internal session\n", encoding="utf-8")
    (workspace / ".pi" / "session-state.json").parent.mkdir()
    (workspace / ".pi" / "session-state.json").write_text(
        "internal state\n", encoding="utf-8"
    )
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)

    results = file_storage.scan_and_upload_workspace("task-1", workspace)

    assert [item["path"] for item in results] == ["慧云科技公司介绍.docx"]



@pytest.mark.parametrize("remote_storage", [False, True])
def test_generated_ppt_repairs_metadata_without_changing_original_or_design(
    tmp_path: Path, monkeypatch, remote_storage: bool,
) -> None:
    from pptx import Presentation
    from pptx.oxml.ns import qn
    from app.services.presentation_deck import normalize_presentation_package

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "新的演示内容"
    slide.placeholders[1].text = "保持原有文字区域和设计"
    slide.notes_slide.notes_text_frame.text = "保留演讲备注"
    for tag in ("p:handoutMasterIdLst", "p:custDataLst"):
        deck._element.append(deck._element.makeelement(qn(tag)))
    buffer = io.BytesIO()
    deck.save(buffer)
    original_bytes = buffer.getvalue()
    workspace = tmp_path / "workspace"
    original = workspace / ".pi" / "attachments" / "original.pptx"
    original.parent.mkdir(parents=True)
    original.write_bytes(original_bytes)
    generated = workspace / "新演示.pptx"
    generated.write_bytes(original_bytes)
    uploads = {}
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: remote_storage)
    monkeypatch.setattr(
        file_storage.minio_storage, "upload_bytes",
        lambda **kwargs: uploads.update({kwargs["object_key"]: kwargs["content"]}),
    )
    file_storage.upload_workspace_file(task_id="ppt-task", workspace=workspace, path=original)
    assert original.read_bytes() == original_bytes
    if remote_storage:
        assert uploads["harness/ppt-task/.pi/attachments/original.pptx"] == original_bytes

    storage_id = f"ppt-task-turn-{'e' * 32}"
    files = file_storage.scan_and_upload_workspace(
        "ppt-task", workspace, storage_task_id=storage_id,
    )
    assert [item["name"] for item in files] == ["新演示.pptx"]
    stored_workspace = file_storage.resolve_workspace(storage_id)
    delivered = (stored_workspace / "新演示.pptx").read_bytes()
    assert original.read_bytes() == original_bytes
    assert generated.read_bytes() == original_bytes
    if remote_storage:
        assert uploads[f"harness/{storage_id}/新演示.pptx"] == delivered
    repaired = Presentation(io.BytesIO(delivered))
    registered = repaired._element.find(qn("p:notesMasterIdLst"))
    assert {node.get(qn("r:id")) for node in registered} == {
        relation.rId for relation in repaired.part.rels.values()
        if relation.reltype.endswith("/notesMaster")
    }
    assert repaired._element.find(qn("p:handoutMasterIdLst")) is None
    assert repaired._element.find(qn("p:custDataLst")) is None
    with zipfile.ZipFile(io.BytesIO(original_bytes)) as before, zipfile.ZipFile(io.BytesIO(delivered)) as after:
        assert before.namelist() == after.namelist()
        for part in before.namelist():
            if part != "ppt/presentation.xml":
                assert before.read(part) == after.read(part), part
    assert normalize_presentation_package(delivered) == delivered


@pytest.mark.parametrize("selected_paths", [set(), {"最终成果.pptx"}])
def test_declared_delivery_omits_original_copies_and_intermediate_assets(
    tmp_path: Path, monkeypatch, selected_paths: set[str],
) -> None:
    from PIL import Image
    from pptx import Presentation

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    deck = Presentation()
    deck.slides.add_slide(deck.slide_layouts[0]).shapes.title.text = "最终成果"
    deck.save(workspace / "最终成果.pptx")
    baseline = file_storage.snapshot_deliverable_files(workspace)
    deck.slides[0].shapes.title.text = "不应交付的原稿副本"
    deck.save(workspace / "原稿副本.pptx")
    Image.new("RGB", (2, 2), "black").save(workspace / "提取图片.png")
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)

    storage_id = f"declared-task-turn-{'d' * 32}"
    files = file_storage.scan_and_upload_workspace(
        "declared-task", workspace, since=baseline,
        storage_task_id=storage_id, selected_paths=selected_paths,
    )
    assert {item["path"] for item in files} == selected_paths
    if selected_paths:
        stored = file_storage.resolve_workspace(storage_id)
        assert Presentation(stored / "最终成果.pptx").slides[0].shapes.title.text == "最终成果"
        assert not (stored / "原稿副本.pptx").exists()
        assert not (stored / "提取图片.png").exists()
    assert Presentation(workspace / "原稿副本.pptx").slides[0].shapes.title.text == "不应交付的原稿副本"


def test_scan_collects_deck_sidecar_but_not_regular_json(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sidecar = workspace / "deck.deck.json"
    sidecar.write_text('{"schemaVersion":1}', encoding="utf-8")
    (workspace / "goal.json").write_text('{"goal":"internal"}', encoding="utf-8")
    (workspace / "package.json").write_text('{"name":"internal"}', encoding="utf-8")
    uploaded_keys: list[str] = []
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: True)
    monkeypatch.setattr(
        file_storage.minio_storage,
        "upload_bytes",
        lambda **kwargs: uploaded_keys.append(kwargs["object_key"]) or "minio://ok",
    )

    results = file_storage.scan_and_upload_workspace("task-deck", workspace)

    assert [item["name"] for item in results] == ["deck.deck.json"]
    assert results[0]["url"] == "/api/files/task-deck/deck.deck.json"
    assert uploaded_keys == ["harness/task-deck/deck.deck.json"]
    assert (
        file_storage.resolve_workspace_file(workspace, "deck.deck.json")
        == sidecar.resolve()
    )

def test_scan_uses_distinct_storage_namespace_per_turn(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.docx").write_bytes(_docx_bytes())
    uploaded_keys: list[str] = []
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: True)
    monkeypatch.setattr(
        file_storage.minio_storage,
        "upload_bytes",
        lambda **kwargs: uploaded_keys.append(kwargs["object_key"]) or "minio://ok",
    )

    first = file_storage.scan_and_upload_workspace(
        "task-1",
        workspace,
        storage_task_id=f"task-1-turn-{'a' * 32}",
    )
    second = file_storage.scan_and_upload_workspace(
        "task-1",
        workspace,
        storage_task_id=f"task-1-turn-{'b' * 32}",
    )

    assert first[0]["url"] != second[0]["url"]
    assert uploaded_keys == [
        f"harness/task-1-turn-{'a' * 32}/report.docx",
        f"harness/task-1-turn-{'b' * 32}/report.docx",
    ]

def test_scan_preserves_each_turn_for_local_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "report.txt"
    target.write_text("第一版", encoding="utf-8")
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)

    first_id = f"task-1-turn-{'a' * 32}"
    second_id = f"task-1-turn-{'b' * 32}"
    first = file_storage.scan_and_upload_workspace(
        "task-1", workspace, storage_task_id=first_id
    )
    target.write_text("第二版", encoding="utf-8")
    second = file_storage.scan_and_upload_workspace(
        "task-1", workspace, storage_task_id=second_id
    )

    assert first[0]["url"] != second[0]["url"]
    first_workspace = file_storage.resolve_workspace(first_id)
    second_workspace = file_storage.resolve_workspace(second_id)
    assert first_workspace is not None
    assert second_workspace is not None
    assert (first_workspace / "report.txt").read_text(encoding="utf-8") == "第一版"
    assert (second_workspace / "report.txt").read_text(encoding="utf-8") == "第二版"


def test_scan_restores_snapshot_registry_after_invalid_file(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    valid = workspace / "valid.txt"
    invalid = workspace / "broken.docx"
    valid.write_text("原始成果", encoding="utf-8")
    invalid.write_bytes(b"not a docx")
    storage_id = f"task-1-turn-{'c' * 32}"

    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)
    monkeypatch.setattr(file_storage, "package_ooxml_staging", lambda _: None)
    monkeypatch.setattr(
        file_storage,
        "convert_markdown_deliverables",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        Path,
        "rglob",
        lambda self, pattern: [valid, invalid] if self == workspace else [],
    )

    results = file_storage.scan_and_upload_workspace(
        "task-1", workspace, storage_task_id=storage_id
    )
    valid.write_text("当前工作区已被覆盖", encoding="utf-8")

    snapshot = file_storage.resolve_workspace(storage_id)
    assert [item["path"] for item in results] == ["valid.txt"]
    assert snapshot is not None
    assert (snapshot / "valid.txt").read_text(encoding="utf-8") == "原始成果"


@pytest.mark.parametrize("persisted_task", [True, False])
def test_turn_snapshot_resolves_after_restart_without_live_workspace_fallback(
    tmp_path: Path, monkeypatch, persisted_task: bool
) -> None:
    from app.core import database
    from app.repositories import harness
    from app.services.pi_runtime import skills

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "report.txt"
    target.write_text("已交付版本", encoding="utf-8")
    storage_id = f"task-restart-turn-{'d' * 32}"
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)
    file_storage.scan_and_upload_workspace(
        "task-restart", workspace, storage_task_id=storage_id
    )
    snapshot = file_storage.resolve_workspace(storage_id)
    target.write_text("下一轮内容", encoding="utf-8")
    monkeypatch.setattr(file_storage, "_workspace_registry", {})
    monkeypatch.setattr(database, "transaction", lambda: nullcontext(object()))
    monkeypatch.setattr(
        harness,
        "get_task",
        lambda _db, task_id: SimpleNamespace(workspace_path=str(workspace))
        if persisted_task and task_id == "task-restart"
        else None,
    )
    monkeypatch.setattr(skills, "workspace_dir_for_agent", lambda *_args: workspace)

    restored = file_storage.resolve_workspace(storage_id)
    assert restored is not None
    assert (restored / "report.txt").read_text(encoding="utf-8") == "已交付版本"

    assert snapshot is not None
    snapshot.rename(tmp_path / "expired-snapshot")
    monkeypatch.setattr(file_storage, "_workspace_registry", {})
    assert file_storage.resolve_workspace(storage_id) is None

def test_scan_with_since_skips_unchanged_leftover_files(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    leftover = workspace / "scaled.png"
    leftover.write_bytes(b"png-v1")
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)

    baseline = file_storage.snapshot_deliverable_files(workspace)
    (workspace / "fresh.png").write_bytes(b"png-new")

    results = file_storage.scan_and_upload_workspace(
        "task-1", workspace, since=baseline
    )

    assert [item["path"] for item in results] == ["fresh.png"]


def test_scan_with_since_reports_modified_existing_file(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "clean.png"
    target.write_bytes(b"png-v1")
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)

    baseline = file_storage.snapshot_deliverable_files(workspace)
    target.write_bytes(b"png-v2-updated")

    results = file_storage.scan_and_upload_workspace(
        "task-1", workspace, since=baseline
    )

    assert [item["path"] for item in results] == ["clean.png"]
    assert results[0]["size"] == len(b"png-v2-updated")


def test_scan_with_since_tracks_deck_sidecar_changes(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sidecar = workspace / "deck.deck.json"
    sidecar.write_text('{"schemaVersion":1}', encoding="utf-8")
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)
    baseline = file_storage.snapshot_deliverable_files(workspace)

    unchanged = file_storage.scan_and_upload_workspace(
        "task-deck", workspace, since=baseline
    )
    sidecar.write_text(
        '{"schemaVersion":1,"title":"更新后的演示文稿"}', encoding="utf-8"
    )
    changed = file_storage.scan_and_upload_workspace(
        "task-deck", workspace, since=baseline
    )

    assert unchanged == []
    assert [item["name"] for item in changed] == ["deck.deck.json"]
    assert changed[0]["url"] == "/api/files/task-deck/deck.deck.json"


def test_scan_with_since_returns_empty_when_nothing_changed(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "image.png").write_bytes(b"png-old")
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)

    baseline = file_storage.snapshot_deliverable_files(workspace)

    results = file_storage.scan_and_upload_workspace(
        "task-1", workspace, since=baseline
    )

    assert results == []


def test_scan_delivers_skill_uploaded_file_unchanged_since_baseline(
    tmp_path: Path, monkeypatch
) -> None:
    """Skill 本轮显式上传的文件即使 mtime 停留在基线前也要交付一次。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "学生打卡记录表.xlsx"
    target.write_bytes(b"xlsx-v1")
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)

    files_baseline = file_storage.snapshot_deliverable_files(workspace)
    uploads_baseline = file_storage.snapshot_skill_uploads(workspace)
    # 之后的某轮：模型重传早先失败轮生成的文件，内容与 mtime 均未变。
    file_storage.record_skill_upload(workspace, target)

    results = file_storage.scan_and_upload_workspace(
        "task-1",
        workspace,
        since=files_baseline,
        skill_uploads_since=uploads_baseline,
    )

    assert [item["path"] for item in results] == ["学生打卡记录表.xlsx"]

    # 下一轮：同一 manifest 条目已进入基线，不再重复交付。
    uploads_now = file_storage.snapshot_skill_uploads(workspace)
    repeat = file_storage.scan_and_upload_workspace(
        "task-1",
        workspace,
        since=files_baseline,
        skill_uploads_since=uploads_now,
    )
    assert repeat == []


def test_skill_upload_manifest_is_not_collected_as_deliverable(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.txt").write_text("成果", encoding="utf-8")
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)
    file_storage.record_skill_upload(workspace, workspace / "report.txt")

    results = file_storage.scan_and_upload_workspace("task-1", workspace)

    assert [item["path"] for item in results] == ["report.txt"]
    manifest = workspace / ".generated-files" / "skill-uploads.jsonl"
    assert manifest.is_file()


def test_upload_skips_invalid_docx(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = workspace / "broken.docx"
    path.write_bytes(b"not a docx")
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)

    assert file_storage.upload_workspace_file(task_id="task-1", workspace=workspace, path=path) is None


def test_scan_packages_ooxml_staging_and_skips_xml_parts(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    staging = workspace / "docx_tmp"
    (staging / "word" / "_rels").mkdir(parents=True)
    (staging / "_rels").mkdir()
    (staging / "[Content_Types].xml").write_text("<Types/>", encoding="utf-8")
    (staging / "_rels" / ".rels").write_text("<Relationships/>", encoding="utf-8")
    (staging / "word" / "document.xml").write_text("<w:document/>", encoding="utf-8")
    (staging / "word" / "styles.xml").write_text("<w:styles/>", encoding="utf-8")
    (workspace / "慧云科技公司介绍.doc").write_text(
        '<html xmlns:o="urn:schemas-microsoft-com:office:office"></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)

    results = file_storage.scan_and_upload_workspace("task-1", workspace)

    assert [item["path"] for item in results] == ["慧云科技公司介绍.docx"]
    packaged = workspace / "慧云科技公司介绍.docx"
    assert packaged.is_file()
    with zipfile.ZipFile(packaged) as archive:
        assert "[Content_Types].xml" in archive.namelist()
        assert "word/document.xml" in archive.namelist()


def test_upload_skips_html_saved_as_doc(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = workspace / "intro.doc"
    path.write_text("<html xmlns:w=\"urn:schemas-microsoft-com:office:word\"></html>", encoding="utf-8")
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)

    assert file_storage.upload_workspace_file(task_id="task-1", workspace=workspace, path=path) is None


def test_upload_falls_back_to_local_url_when_minio_fails(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = workspace / "report.docx"
    path.write_bytes(_docx_bytes())
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: True)
    monkeypatch.setattr(
        file_storage.minio_storage,
        "upload_bytes",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("SignatureDoesNotMatch")),
    )

    result = file_storage.upload_workspace_file(
        task_id="task-1", workspace=workspace, path=path
    )

    assert result is not None
    assert result["name"] == "report.docx"
    assert result["url"] == "/api/files/task-1/report.docx"


def test_upload_uses_backend_download_url_when_minio_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = workspace / "report.docx"
    path.write_bytes(_docx_bytes())
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: True)
    monkeypatch.setattr(
        file_storage.minio_storage,
        "upload_bytes",
        lambda **kwargs: "minio://dgmeta/harness/task-1/report.docx",
    )
    monkeypatch.setattr(
        file_storage.minio_storage,
        "presign_get_url",
        lambda **kwargs: "http://localhost:19000/invalid-signature",
    )

    result = file_storage.upload_workspace_file(
        task_id="task-1", workspace=workspace, path=path
    )

    assert result is not None
    assert result["url"] == "/api/files/task-1/report.docx"


def test_resolve_workspace_file_accepts_legacy_files_prefix(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "慧云科技公司介绍.docx"
    target.write_bytes(_docx_bytes())

    assert file_storage.resolve_workspace_file(workspace, "慧云科技公司介绍.docx") == target.resolve()
    assert (
        file_storage.resolve_workspace_file(workspace, "files/慧云科技公司介绍.docx")
        == target.resolve()
    )
    assert file_storage.resolve_workspace_file(workspace, "../outside.docx") is None


@pytest.mark.parametrize("explicit", [False, True])
def test_scan_converts_markdown_deliverable_to_docx(
    tmp_path: Path, monkeypatch, explicit: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "介绍.md").write_bytes(
        "# 缔智元\n\n2022 年成立。".encode("utf-8")
    )
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)
    baseline = file_storage.snapshot_deliverable_files(workspace) if explicit else None
    if explicit:
        (workspace / "分析草稿.md").write_text("不应交付的中间分析", encoding="utf-8")

    results = file_storage.scan_and_upload_workspace(
        "task-md", workspace, since=baseline,
        selected_paths={"介绍.md"} if explicit else None,
    )

    assert [item["path"] for item in results] == ["介绍.docx"]
    assert (workspace / "介绍.md").is_file(), "source markdown must survive"
    packaged = workspace / "介绍.docx"
    with zipfile.ZipFile(packaged) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
    assert "缔智元" in document
    if explicit:
        assert not (workspace / "分析草稿.docx").exists()


def test_scan_renders_only_exact_cited_sources_and_preserves_source_record(
    tmp_path: Path, monkeypatch,
) -> None:
    workspace = tmp_path / "source-workspace"
    workspace.mkdir()
    citation_id = f"writing-evidence:ver_original:{'a' * 64}:0-8"
    source = {
        "id": citation_id, "citation_id": citation_id,
        "document_id": "doc_original", "document_version_id": "ver_original",
        "document_name": "原始资质证书", "version_number": 1,
        "text_sha256": "a" * 64, "char_start": 0, "char_end": 8,
        "snapshot_status": "verified", "source_text": "原始证书登记内容",
    }
    (workspace / "引用.md").write_text(
        f"# 引用报告\n\n该证书已登记。^[{citation_id}]\n\n旧绑定[citation:0]",
        encoding="utf-8",
    )
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)
    results = file_storage.scan_and_upload_workspace(
        "source-task", workspace,
        evidence_source_resolver=lambda texts: [source],
    )
    delivered = next(item for item in results if item["path"] == "引用.docx")
    assert delivered["evidence_sources"] == [source]
    with zipfile.ZipFile(workspace / "引用.docx") as archive:
        document = archive.read("word/document.xml").decode("utf-8")
    # 交付正文不写引用：机器 ID、^[] 与来源描述都不进入 DOCX；只保留正文。
    assert "该证书已登记" in document
    assert "原始资质证书" not in document
    assert "原始证书登记内容" not in document
    assert "[1]" not in document
    assert "历史来源不可用" not in document
    assert "writing-evidence:" not in document
    assert "citation:0" not in document


def test_scan_md_upload_falls_back_when_docx_is_foreign(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.md").write_text("# 笔记\n", encoding="utf-8")
    foreign = workspace / "notes.docx"
    foreign.write_bytes(b"PK\x03\x04 not-a-real-docx")
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)

    results = file_storage.scan_and_upload_workspace("task-md2", workspace)

    # The foreign docx is not ours and cannot be regenerated; the markdown
    # itself remains the deliverable instead of being silently swallowed.
    assert [item["path"] for item in results] == ["notes.md"]


def test_convert_markdown_regenerates_only_own_rendered_docx(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "doc.md").write_text("# 文档\n", encoding="utf-8")

    file_storage.convert_markdown_deliverables(workspace, None)
    own_bytes = (workspace / "doc.docx").read_bytes()

    # A foreign Word file with our name must survive a re-conversion and the
    # markdown is reported as NOT converted (delivery falls back to the .md).
    (workspace / "doc.docx").write_bytes(b"PK\x03\x04 foreign-bytes")
    file_storage.convert_markdown_deliverables(workspace, None)
    assert (workspace / "doc.docx").read_bytes() == b"PK\x03\x04 foreign-bytes"

    # Restore our own rendered output, then update the markdown: the next
    # conversion regenerates the docx from the new content.
    (workspace / "doc.docx").write_bytes(own_bytes)
    (workspace / "doc.md").write_text("# 文档 v2\n", encoding="utf-8")
    file_storage.convert_markdown_deliverables(workspace, None)
    with zipfile.ZipFile(workspace / "doc.docx") as archive:
        assert "文档 v2" in archive.read("word/document.xml").decode("utf-8")


def test_scan_keeps_md_when_own_docx_conversion_fails(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "doc.md").write_text("# 文档\n", encoding="utf-8")
    file_storage.convert_markdown_deliverables(workspace, None)
    stale = (workspace / "doc.docx").read_bytes()

    # Real runner flow: snapshot the baseline, then update the markdown.
    baseline = file_storage.snapshot_deliverable_files(workspace)
    (workspace / "doc.md").write_text("# 更新文档\n", encoding="utf-8")
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)
    monkeypatch.setattr(
        file_storage,
        "markdown_to_docx_bytes",
        lambda markdown, **kwargs: (_ for _ in ()).throw(ValueError("boom")),
    )

    results = file_storage.scan_and_upload_workspace(
        "task-md4", workspace, since=baseline
    )

    # Conversion failed, so the updated markdown must still be delivered
    # instead of the stale unchanged docx or nothing at all.
    assert [item["path"] for item in results] == ["doc.md"]
    assert (workspace / "doc.docx").read_bytes() == stale


def test_scan_md_skips_conversion_when_unchanged_since_baseline(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "old.md"
    source.write_text("# 旧文档\n", encoding="utf-8")
    baseline = file_storage.snapshot_deliverable_files(workspace)
    monkeypatch.setattr(file_storage.minio_storage, "is_minio_enabled", lambda: False)

    results = file_storage.scan_and_upload_workspace("task-md3", workspace, since=baseline)

    assert results == []
    assert not (workspace / "old.docx").exists()


def test_rewrite_markdown_paths_returns_string_and_maps_paths() -> None:
    rewrite = file_storage.rewrite_markdown_paths_to_docx
    # Nested relative path mention.
    nested = rewrite(
        "已写入 /workspace/a/报告.md",
        [{"name": "报告.docx", "path": "a/报告.docx"}],
    )
    assert nested == "已写入 /workspace/a/报告.docx"
    # Same stem in different directories is not cross-rewritten.
    both = rewrite(
        "/workspace/docs/v1/报告.md 与 /workspace/final/报告.md",
        [
            {"name": "报告.docx", "path": "docs/v1/报告.docx"},
            {"name": "报告.docx", "path": "final/报告.docx"},
        ],
    )
    assert both == "/workspace/docs/v1/报告.docx 与 /workspace/final/报告.docx"

    # A slash-prefixed mention of a sibling file must NOT be rewritten when
    # only a nested same-stem docx was delivered (advisor regression case).
    sibling = rewrite(
        "/workspace/b/报告.md 有问题",
        [{"name": "报告.docx", "path": "a/报告.docx"}],
    )
    assert sibling == "/workspace/b/报告.md 有问题"
    # Always returns a str, even with no files.
    assert rewrite("普通回复", None) == "普通回复"
    assert rewrite("", []) == ""


def test_rewrite_removes_delivery_narration_but_preserves_technical_workspace_paths() -> None:
    rewrite = file_storage.rewrite_markdown_paths_to_docx

    observed = rewrite(
        "正文 215 个中文字（合计约 260 字），成稿已存 /workspace/aimpleee/intro.docx。",
        [{"name": "intro.docx", "path": "intro.docx"}],
    )
    assert observed == "正文 215 个中文字（合计约 260 字）"

    existing = rewrite(
        "Word 文档已生成，文件路径：`/workspace/销售合同.docx`",
        [{"name": "销售合同.docx", "path": "销售合同.docx"}],
    )
    assert existing == "Word 文档已生成"

    html = rewrite(
        "已完成。\n\n**文件位置：** `/workspace/shenglang-h5.html`\n\n下载后打开。",
        [{"name": "shenglang-h5.html", "path": "shenglang-h5.html"}],
    )
    assert html == "已完成。\n\n\n\n下载后打开。"

    assert rewrite("文件位置：/workspace/shenglang-h5.html", []) == ""
    assert rewrite("**文件路径**：`/workspace/shenglang-h5.html`", []) == ""

    technical = rewrite(
        "技术说明：输入文件位于 /workspace/aimpleee/source.txt。",
        [{"name": "intro.docx", "path": "intro.docx"}],
    )
    assert technical == "技术说明：输入文件位于 /workspace/aimpleee/source.txt。"

    technical_label = rewrite(
        "技术说明：配置文件路径：/workspace/app/config.yaml。",
        [{"name": "intro.docx", "path": "intro.docx"}],
    )
    assert technical_label == "技术说明：配置文件路径：/workspace/app/config.yaml。"

    fenced = rewrite(
        "```text\n文件路径：/workspace/aimpleee/example.txt\n```",
        [{"name": "intro.docx", "path": "intro.docx"}],
    )
    assert fenced == "```text\n文件路径：/workspace/aimpleee/example.txt\n```"
