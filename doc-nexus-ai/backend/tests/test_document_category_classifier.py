from types import SimpleNamespace

from app.models.access import DocumentCategory
from app.services.document_category_classifier import classify_by_rules, classify_with_llm


def _category(category_id: str, name: str, *, path: str | None = None, parent_id: str | None = None):
    return DocumentCategory(
        id=category_id,
        space_id="space-1",
        parent_id=parent_id,
        name=name,
        sort_order=0,
        path=path or name,
    )


def test_classify_by_rules_prefers_source_directory():
    categories = [
        _category("cat-qualification", "资质文件-来酷智联"),
        _category("cat-contract", "销售合同-来酷智联"),
    ]

    assert (
        classify_by_rules(
            categories,
            filename="营业执照.pdf",
            source_relative_path="资质文件-来酷智联/营业执照.pdf",
        )
        == "cat-qualification"
    )


def test_classify_by_rules_matches_all_filename_keywords():
    categories = [
        _category("cat-contract", "销售合同-来酷智联"),
        _category("cat-qualification", "资质文件-来酷智联"),
    ]

    assert (
        classify_by_rules(
            categories,
            filename="来酷智联销售合同2024.pdf",
        )
        == "cat-contract"
    )


def test_classify_by_rules_returns_none_for_tie_or_no_signal():
    tied = [
        _category("cat-tech", "技术文档"),
        _category("cat-product", "产品文档"),
    ]

    assert classify_by_rules(tied, filename="技术文档产品文档.pdf") is None
    assert classify_by_rules(tied, filename="会议纪要.pdf") is None


def test_classify_with_llm_accepts_only_high_confidence_space_category(monkeypatch):
    categories = [_category("cat-contract", "销售合同-来酷智联")]
    endpoint = SimpleNamespace(timeout_ms=30_000)
    monkeypatch.setattr(
        "app.services.document_category_classifier._call_llm",
        lambda *args, **kwargs: (
            '{"category_id":"cat-contract","confidence":0.9,"reason":"合同"}',
            {},
        ),
    )

    assert (
        classify_with_llm(
            categories,
            endpoint=endpoint,
            filename="合同.pdf",
            document_name="销售合同",
            source_relative_path=None,
            text="双方签署销售合同。",
            user_id="usr-1",
        )
        == "cat-contract"
    )


def test_classify_with_llm_rejects_invalid_or_low_confidence_result(monkeypatch):
    categories = [_category("cat-contract", "销售合同-来酷智联")]
    endpoint = SimpleNamespace(timeout_ms=30_000)
    responses = iter(
        [
            '{"category_id":"cat-other","confidence":0.99}',
            '{"category_id":"cat-contract","confidence":0.69}',
        ]
    )
    monkeypatch.setattr(
        "app.services.document_category_classifier._call_llm",
        lambda *args, **kwargs: (next(responses), {}),
    )

    assert (
        classify_with_llm(
            categories,
            endpoint=endpoint,
            filename="合同.pdf",
            document_name=None,
            source_relative_path=None,
            text="合同正文",
            user_id="usr-1",
        )
        is None
    )
    assert (
        classify_with_llm(
            categories,
            endpoint=endpoint,
            filename="合同.pdf",
            document_name=None,
            source_relative_path=None,
            text="合同正文",
            user_id="usr-1",
        )
        is None
    )
