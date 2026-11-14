"""Live decoding of one Markdown field out of a streamed model JSON reply.

A writing chapter is streamed as raw text deltas of a JSON document. The final
payload parser (``parse_json_reply``) stays the authority for what gets saved;
this module only reconstructs an early, honest preview of the single field a
reader cares about — ``sections[0].markdown`` — while the model is still writing.

It never invents text: an ambiguous or invalid prefix is reported as
``unavailable`` instead of being guessed at, while unescaped quotes and raw
control characters inside Markdown are tolerated exactly the way the final
parser repairs them.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

PreviewStatus = Literal[
    "preparing",
    "streaming",
    "validating",
    "saved",
    "candidate",
    "failed",
    "cancelled",
    "unavailable",
]
PREVIEW_STATUSES: tuple[PreviewStatus, ...] = (
    "preparing",
    "streaming",
    "validating",
    "saved",
    "candidate",
    "failed",
    "cancelled",
    "unavailable",
)
ACTIVE_PREVIEW_STATUSES = frozenset({"preparing", "streaming", "validating"})
TERMINAL_PREVIEW_STATUSES = frozenset(PREVIEW_STATUSES) - ACTIVE_PREVIEW_STATUSES

#: Snapshot writes/emits are throttled to this interval; stage transitions and
#: the terminal snapshot always force one through.
PREVIEW_EMIT_INTERVAL_SECONDS = 0.75

#: A preview never needs to carry more Markdown than a chapter body may hold —
#: the same ceiling ``ChapterSaveRequest.content`` enforces on save.
PREVIEW_MARKDOWN_LIMIT = 200_000

PREVIEW_UNAVAILABLE_MESSAGE = "无法从模型流式输出中恢复本章预览；完成后的正文不受影响。"
PREVIEW_FAILED_MESSAGE = "本章生成失败，可重试。"

_JSON_WHITESPACE = " \t\r\n"
_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_LITERALS = frozenset({"true", "false", "null"})
_NUMBER = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?")
_HEX4 = re.compile(r"[0-9a-fA-F]{4}")
_NUMBER_CHARS = frozenset("0123456789+-.eE")
_ALPHA_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")

#: ``sections[0].markdown`` — the one field a chapter preview shows.
CHAPTER_MARKDOWN_PATH: tuple[Any, ...] = ("sections", 0, "markdown")


class MalformedPreviewJSON(ValueError):
    """The streamed reply stopped being valid JSON before the target string."""


def _printable(char: str) -> str:
    """Raw control characters are content the final parser escapes for us."""
    if char >= " " or char in "\n\r\t":
        return char
    return " "


class MarkdownStreamDecoder:
    """Incremental decoder for one JSON string value inside a partial reply.

    The reply arrives in arbitrary chunks, so every token that is not the target
    string is only consumed once it is unambiguously complete; the target string
    is decoded as far as the buffered characters allow and resumed later.
    """

    def __init__(self, target: tuple[Any, ...] = CHAPTER_MARKDOWN_PATH) -> None:
        self._target = target
        self._buffer = ""
        self._pos = 0
        self._frames: list[_Frame] = []
        self._root_started = False
        self._complete = False
        self._failed = False
        self._in_target = False
        self._decoded: list[str] = []

    @property
    def markdown(self) -> str:
        if self._failed:
            return ""
        return "".join(self._decoded)

    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def target(self) -> tuple[Any, ...]:
        return self._target

    @property
    def complete(self) -> bool:
        return self._complete

    def feed(self, text: str) -> None:
        if not text or self._failed or self._complete:
            return
        self._buffer += text
        try:
            self._run()
        except MalformedPreviewJSON:
            self._failed = True

    # ── structural scan ────────────────────────────────────────────

    def _run(self) -> None:
        buffer = self._buffer
        while True:
            if self._in_target:
                self._consume_target_string()
                if self._in_target:
                    return
                # The target value is complete: nothing after it matters, and
                # stopping keeps a re-emitted reply from rewriting the preview.
                self._complete = True
                return
            pos = self._pos
            length = len(buffer)
            while pos < length and buffer[pos] in _JSON_WHITESPACE:
                pos += 1
            if pos >= length:
                self._pos = pos
                return
            self._pos = pos
            char = buffer[pos]
            if not self._root_started:
                if char != "{":
                    # Leading prose or a code fence before the JSON object.
                    self._pos = pos + 1
                    continue
                self._root_started = True
                self._pos = pos + 1
                self._frames.append(_Frame("object", ()))
                continue
            if not self._frames:
                self._complete = True
                return
            frame = self._frames[-1]
            scan_from = self._pos
            if frame.kind == "object":
                self._scan_object(frame, pos, char)
            else:
                self._scan_array(frame, pos, char)
            if self._pos == scan_from:
                # Nothing was consumed: this token is undecided until more of
                # the stream arrives. Looping here would spin forever.
                return

    def _scan_object(self, frame: _Frame, pos: int, char: str) -> None:
        if frame.state == "key":
            if char == "}":
                self._pos = pos + 1
                self._frames.pop()
                return
            if char != '"':
                raise MalformedPreviewJSON("object key must be a string")
            token = self._read_string_token(pos, is_key=True)
            if token is None:
                return
            self._pos, frame.key = token[0], token[1]
            frame.state = "colon"
            return
        if frame.state == "colon":
            if char != ":":
                raise MalformedPreviewJSON("object key without a value")
            self._pos = pos + 1
            frame.state = "value"
            return
        if frame.state == "comma":
            if char == ",":
                self._pos = pos + 1
                frame.state = "key"
                return
            if char == "}":
                self._pos = pos + 1
                self._frames.pop()
                return
            raise MalformedPreviewJSON("object member without a separator")
        value_path = frame.path + (frame.key,)
        if not self._start_value(pos, value_path):
            # An undecided token keeps the member pending; the next delta
            # resumes here instead of the state machine skipping ahead.
            return
        frame.state = "comma"

    def _scan_array(self, frame: _Frame, pos: int, char: str) -> None:
        if frame.state == "comma":
            if char == ",":
                self._pos = pos + 1
                frame.state = "value"
                return
            if char == "]":
                self._pos = pos + 1
                self._frames.pop()
                return
            raise MalformedPreviewJSON("array item without a separator")
        if char == "]":
            self._pos = pos + 1
            self._frames.pop()
            return
        value_path = frame.path + (frame.index,)
        if not self._start_value(pos, value_path):
            return
        frame.index += 1
        frame.state = "comma"

    def _start_value(self, pos: int, value_path: tuple[Any, ...]) -> bool:
        """Consume the start of a value; ``False`` while it may still grow."""
        char = self._buffer[pos]
        if char == "{":
            self._pos = pos + 1
            self._frames.append(_Frame("object", value_path))
            return True
        if char == "[":
            self._pos = pos + 1
            self._frames.append(_Frame("array", value_path))
            return True
        if char == '"':
            if value_path == self._target:
                self._pos = pos + 1
                self._decoded = []
                self._in_target = True
                return True
            token = self._read_string_token(pos)
            # An incomplete token leaves the scan position on its opening quote
            # so the next delta re-reads it from the start.
            if token is None:
                return False
            self._pos = token[0]
            return True
        if char in _NUMBER_CHARS or char in _ALPHA_CHARS:
            token = self._read_literal_token(pos)
            if token is None:
                return False
            self._pos = token
            return True
        raise MalformedPreviewJSON("unexpected character in JSON value")

    # ── tokens ─────────────────────────────────────────────────────

    def _consume_target_string(self) -> None:
        buffer = self._buffer
        length = len(buffer)
        index = self._pos
        pieces: list[str] = []
        while index < length:
            char = buffer[index]
            if char == '"':
                closes = self._closes_string(index)
                if closes is None:
                    break
                if closes:
                    index += 1
                    self._pos = index
                    self._in_target = False
                    _extend(self._decoded, pieces)
                    return
                pieces.append('"')
                index += 1
                continue
            if char == "\\":
                escape = self._read_escape(index)
                if escape is None:
                    break
                index, piece = escape
                pieces.append(piece)
                continue
            pieces.append(_printable(char))
            index += 1
        self._pos = index
        _extend(self._decoded, pieces)

    def _read_string_token(self, start: int, *, is_key: bool = False) -> tuple[int, str] | None:
        """Decode a complete string token, or ``None`` while it may still grow."""
        buffer = self._buffer
        length = len(buffer)
        index = start + 1
        pieces: list[str] = []
        while index < length:
            char = buffer[index]
            if char == '"':
                closes = self._closes_string(index, is_key=is_key)
                if closes is None:
                    return None
                if closes:
                    return index + 1, "".join(pieces)
                pieces.append('"')
                index += 1
                continue
            if char == "\\":
                escape = self._read_escape(index)
                if escape is None:
                    return None
                index, piece = escape
                pieces.append(piece)
                continue
            pieces.append(_printable(char))
            index += 1
        return None

    def _read_literal_token(self, start: int) -> int | None:
        buffer = self._buffer
        length = len(buffer)
        index = start
        while index < length and (
            buffer[index] in _NUMBER_CHARS or buffer[index] in _ALPHA_CHARS
        ):
            index += 1
        if index >= length:
            return None
        token = buffer[start:index]
        if token not in _LITERALS and _NUMBER.fullmatch(token) is None:
            raise MalformedPreviewJSON("invalid JSON literal")
        return index

    def _closes_string(self, index: int, *, is_key: bool = False) -> bool | None:
        """Whether the quote at ``index`` ends the string, ``None`` if undecided."""
        buffer = self._buffer
        cursor = index + 1
        length = len(buffer)
        while cursor < length and buffer[cursor] in _JSON_WHITESPACE:
            cursor += 1
        if cursor >= length:
            return None
        return buffer[cursor] == ":" if is_key else buffer[cursor] in ",]}"

    def _read_escape(self, index: int) -> tuple[int, str] | None:
        buffer = self._buffer
        length = len(buffer)
        if index + 1 >= length:
            return None
        marker = buffer[index + 1]
        simple = _ESCAPES.get(marker)
        if simple is not None:
            return index + 2, simple
        if marker != "u":
            raise MalformedPreviewJSON("invalid escape sequence")
        if index + 6 > length:
            return None
        digits = buffer[index + 2 : index + 6]
        if _HEX4.fullmatch(digits) is None:
            raise MalformedPreviewJSON("invalid unicode escape")
        code = int(digits, 16)
        if 0xD800 <= code <= 0xDBFF:
            pair_end = index + 12
            if pair_end > length:
                return None
            if (
                buffer[index + 6] == "\\"
                and buffer[index + 7] == "u"
                and _HEX4.fullmatch(buffer[index + 8 : index + 12]) is not None
            ):
                low = int(buffer[index + 8 : index + 12], 16)
                if 0xDC00 <= low <= 0xDFFF:
                    return pair_end, chr(
                        0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                    )
            # A lone surrogate cannot be encoded; never fail the whole preview
            # over one broken emoji.
            return index + 6, "\ufffd"
        if 0xDC00 <= code <= 0xDFFF:
            return index + 6, "\ufffd"
        return index + 6, chr(code)


class _Frame:
    """One open JSON object/array with its pending position."""

    __slots__ = ("kind", "state", "key", "index", "path")

    def __init__(self, kind: str, path: tuple[Any, ...]) -> None:
        self.kind = kind
        self.state = "key" if kind == "object" else "value"
        self.key: str | None = None
        self.index = 0
        self.path = path


def _extend(target: list[str], pieces: list[str]) -> None:
    if pieces:
        target.append("".join(pieces))
        pieces.clear()


# ── snapshot ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChapterPreviewSnapshot:
    """The full cumulative preview of one chapter attempt."""

    operation_id: str
    chapter_id: str
    preview_id: str
    attempt: int
    base_revision: int
    event_id: int
    status: PreviewStatus
    markdown: str
    candidate_id: str | None
    revision: int | None
    error_message: str | None
    updated_at: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "chapter_id": self.chapter_id,
            "preview_id": self.preview_id,
            "attempt": self.attempt,
            "base_revision": self.base_revision,
            "event_id": self.event_id,
            "status": self.status,
            "markdown": self.markdown,
            "candidate_id": self.candidate_id,
            "revision": self.revision,
            "error_message": self.error_message,
            "updated_at": self.updated_at,
        }


def _positive_int(value: Any, default: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value if value > 0 else default


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def decode_snapshot(payload_json: str | None) -> ChapterPreviewSnapshot | None:
    """Read a persisted snapshot payload; junk is dropped instead of guessed."""
    if not payload_json:
        return None
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError):
        return None
    preview = payload.get("preview") if isinstance(payload, dict) else None
    if not isinstance(preview, dict):
        return None
    operation_id = _optional_str(preview.get("operation_id"))
    chapter_id = _optional_str(preview.get("chapter_id"))
    preview_id = _optional_str(preview.get("preview_id"))
    status: Any = _optional_str(preview.get("status"))
    if not operation_id or not chapter_id or not preview_id:
        return None
    if status not in PREVIEW_STATUSES:
        return None
    markdown = preview.get("markdown")
    return ChapterPreviewSnapshot(
        operation_id=operation_id,
        chapter_id=chapter_id,
        preview_id=preview_id,
        attempt=_positive_int(preview.get("attempt")),
        base_revision=_positive_int(preview.get("base_revision")),
        event_id=_optional_int(preview.get("event_id")) or 0,
        status=status,
        markdown=markdown if isinstance(markdown, str) else "",
        candidate_id=_optional_str(preview.get("candidate_id")),
        revision=_optional_int(preview.get("revision")),
        error_message=_optional_str(preview.get("error_message")),
        updated_at=_optional_str(preview.get("updated_at")) or "",
    )


def chapter_preview_id(
    operation_id: str, chapter_id: str, attempt: int, round_number: int = 1
) -> str:
    """Identity of one streamed model output.

    A retry (new attempt) or a restarted output (new round inside the same
    attempt, e.g. another assistant message after a tool round) gets a fresh
    identity, so stale text can never be merged into the newest one.
    """
    return (
        f"{operation_id}:{chapter_id}"
        f":a{max(1, int(attempt))}r{max(1, int(round_number))}"
    )


class ChapterPreviewStream:
    """Decoded Markdown of one chapter attempt, with write throttling."""

    def __init__(
        self,
        *,
        operation_id: str,
        chapter_id: str,
        attempt: int,
        base_revision: int,
        decoder: MarkdownStreamDecoder | None = None,
        interval_seconds: float = PREVIEW_EMIT_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.operation_id = operation_id
        self.chapter_id = chapter_id
        self.attempt = max(1, int(attempt))
        self.base_revision = max(1, int(base_revision))
        self.preview_id = chapter_preview_id(operation_id, chapter_id, self.attempt)
        self.status: PreviewStatus = "preparing"
        self.candidate_id: str | None = None
        self.revision: int | None = None
        self.error_message: str | None = None
        self.event_id = 0
        self._decoder = decoder or MarkdownStreamDecoder()
        self._interval = max(0.1, float(interval_seconds))
        self._clock = clock
        self._last_emit = float("-inf")
        self._unavailable = False
        self._final_markdown: str | None = None
        self._round = 1
        self._saw_output = False

    @property
    def markdown(self) -> str:
        if self._final_markdown is not None:
            return self._final_markdown
        return self._decoder.markdown

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_PREVIEW_STATUSES

    def feed(self, text: str) -> None:
        """Fold one streamed model text delta into the preview."""
        if not text or not self.active:
            return
        if text.strip():
            self._saw_output = True
        self._decoder.feed(text)
        if self._decoder.failed:
            if not self._unavailable:
                self._unavailable = True
                self.status = "unavailable"
                self.error_message = PREVIEW_UNAVAILABLE_MESSAGE
            return
        if not self._decoder.complete:
            self.status = "streaming"

    def supersede(self) -> bool:
        """Start a fresh output identity for a new assistant/tool round.

        Returns ``True`` when a restart already produced text and was therefore
        replaced; the caller must publish the new identity immediately.
        """
        if not self._saw_output:
            return False
        self._round += 1
        self.preview_id = chapter_preview_id(
            self.operation_id, self.chapter_id, self.attempt, self._round
        )
        self._decoder = MarkdownStreamDecoder(self._decoder.target)
        self._unavailable = False
        self._final_markdown = None
        self._saw_output = False
        self.status = "preparing"
        self.candidate_id = None
        self.revision = None
        self.error_message = None
        return True

    def transition(
        self,
        status: PreviewStatus,
        *,
        markdown: str | None = None,
        candidate_id: str | None = None,
        revision: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Move to a new stage; the caller persists it (transitions always force)."""
        self.status = status
        self.candidate_id = candidate_id
        self.revision = revision
        self.error_message = error_message
        if markdown is not None:
            self._final_markdown = markdown

    def due(self, *, force: bool = False) -> bool:
        if force:
            return True
        return self._clock() - self._last_emit >= self._interval

    def mark_emitted(self, *, event_id: int) -> None:
        self.event_id = int(event_id)
        self._last_emit = self._clock()

    def snapshot(self, *, event_id: int, updated_at: str) -> dict[str, Any]:
        return ChapterPreviewSnapshot(
            operation_id=self.operation_id,
            chapter_id=self.chapter_id,
            preview_id=self.preview_id,
            attempt=self.attempt,
            base_revision=self.base_revision,
            event_id=event_id,
            status=self.status,
            markdown=self.markdown[:PREVIEW_MARKDOWN_LIMIT],
            candidate_id=self.candidate_id,
            revision=self.revision,
            error_message=self.error_message[:500] if self.error_message else None,
            updated_at=updated_at,
        ).as_payload()
