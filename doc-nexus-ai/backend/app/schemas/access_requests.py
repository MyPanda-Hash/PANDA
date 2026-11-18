"""Request contracts for the access-request flow.

The security contract is enforced here, at the schema layer, because "reject
unknown ids" cannot be implemented anywhere else:

* the outer models forbid extra fields — most request models in this codebase
  do not, and would silently drop them;
* ``source`` is a *discriminated union*, not a dict, so ``document_id`` nested
  inside ``{"kind": "writing", ...}`` is a 422 instead of being swallowed;
* ``citation_id`` is only meaningful for anchors that have a trustworthy
  citation set, so the cross-field rule rejects it elsewhere.

Response models live with the endpoints that produce them.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SOURCE_KIND_WRITING = "writing"
SOURCE_KIND_WRITING_PUBLICATION = "writing_publication"
SOURCE_KIND_CHAT_PUBLICATION = "chat_publication"
SOURCE_KIND_DISCOVERY = "discovery"

#: Anchors whose citations are server-recorded and may therefore be named by
#: the client. ``chat_publication`` has no trustworthy document-level set, and
#: ``discovery`` targets a single object through its own handle.
CITATION_SOURCE_KINDS = frozenset(
    {SOURCE_KIND_WRITING, SOURCE_KIND_WRITING_PUBLICATION}
)

_ID_FIELD = Field(min_length=1, max_length=64)


class WritingSource(BaseModel):
    """A live writing project the caller can read."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["writing"] = "writing"
    project_id: str = _ID_FIELD


class WritingPublicationSource(BaseModel):
    """A frozen shared writing snapshot."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["writing_publication"] = "writing_publication"
    publication_id: str = _ID_FIELD


class ChatPublicationSource(BaseModel):
    """A frozen shared chat snapshot (space-level provenance only)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["chat_publication"] = "chat_publication"
    publication_id: str = _ID_FIELD


class DiscoverySource(BaseModel):
    """A server-issued discovery handle."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["discovery"] = "discovery"
    discovery_id: str = _ID_FIELD


AccessRequestSource = Annotated[
    WritingSource | WritingPublicationSource | ChatPublicationSource | DiscoverySource,
    Field(discriminator="kind"),
]


class _AccessRequestBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: AccessRequestSource
    citation_id: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def _citation_requires_a_citable_anchor(self) -> "_AccessRequestBase":
        if self.citation_id is not None and self.source.kind not in CITATION_SOURCE_KINDS:
            raise ValueError("citation_id 只对写作项目与共享文稿有效")
        return self


class AccessRequestResolveRequest(_AccessRequestBase):
    """Diagnosis only: says what could be requested, never grants anything."""


class AccessRequestSubmitRequest(_AccessRequestBase):
    """One submission may cover several targets; the server enumerates them."""

    reason: str | None = Field(default=None, max_length=500)


CitationState = Literal["missing_access", "stale", "out_of_scope", "already_allowed"]
TargetState = Literal["missing_access", "pending"]


class AccessTargetResponse(BaseModel):
    """One requestable target. Names only — never the target's id.

    The client cannot echo a target id back (the server re-derives it), and a
    name is the single fact the requester needs to know whom to ask.
    """

    target_type: str
    target_name: str
    owner_display_name: str | None = None
    state: TargetState = "missing_access"


class AccessRequestResolveResponse(BaseModel):
    """Diagnosis only. ``citation_state`` is the verdict for the citation the
    caller named (null for anchors without a citation dimension), and it must
    never be inferred from ``targets`` being empty: "nothing to request" and
    "this citation is dead" are different answers.
    """

    citation_state: CitationState | None = None
    targets: list[AccessTargetResponse] = Field(default_factory=list)
    other_scope_blocked: bool = False
    requestable: bool = False
