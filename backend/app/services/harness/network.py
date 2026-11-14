"""Controlled outbound HTTP broker for approved Harness capabilities."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MODEL_RESPONSE_BODY_MAX_BYTES = 32 * 1024

_MODEL_RESPONSE_HEADERS = frozenset(
    {
        "content-disposition",
        "content-encoding",
        "content-language",
        "content-length",
        "content-range",
        "content-type",
        "etag",
        "last-modified",
        "location",
        "retry-after",
    }
)
_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
_HTML_IGNORED_ELEMENTS = frozenset({"script", "style"})
_HTML_BLOCK_ELEMENTS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "caption",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "title",
        "tr",
        "ul",
    }
)


class _ReadableHTMLParser(HTMLParser):
    """提取可见正文，不执行也不返回脚本和样式等活动内容。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        normalized = tag.lower()
        if normalized in _HTML_IGNORED_ELEMENTS:
            self._ignored_depth += 1
        elif self._ignored_depth == 0 and normalized in _HTML_BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in _HTML_IGNORED_ELEMENTS:
            if self._ignored_depth:
                self._ignored_depth -= 1
        elif self._ignored_depth == 0 and normalized in _HTML_BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self._parts.append(data)

    def readable_text(self) -> str:
        lines = (
            " ".join(line.split())
            for line in "".join(self._parts).splitlines()
        )
        return "\n".join(line for line in lines if line)


def _header_value(headers: dict[str, str], name: str) -> str:
    normalized = name.lower()
    return next(
        (value for key, value in headers.items() if key.lower() == normalized),
        "",
    )


def _looks_like_html(headers: dict[str, str], body: bytes) -> bool:
    content_type = _header_value(headers, "content-type").lower()
    if any(candidate in content_type for candidate in _HTML_CONTENT_TYPES):
        return True
    prefix = body.lstrip().removeprefix(b"\xef\xbb\xbf")[:256].lower()
    return prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


def _extract_readable_html(body: bytes) -> str:
    parser = _ReadableHTMLParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    parser.close()
    return parser.readable_text()


def _truncate_utf8(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


class NetworkSecurityError(RuntimeError):
    """Outbound request failed a capability or SSRF safety check."""


def _resolve_addresses(host: str, port: int | None) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise NetworkSecurityError("cannot resolve URL host") from exc
    addresses = sorted({str(info[4][0]) for info in infos})
    if not addresses:
        raise NetworkSecurityError("URL host has no usable address")
    return addresses


def reject_private_host(host: str, port: int | None = None) -> str:
    """Raise if *host* resolves to a private or reserved address.

    Shared SSRF guard used by the network broker and the Skill source
    fetcher; both must refuse to contact internal infrastructure.
    """
    for address in _resolve_addresses(host, port):
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise NetworkSecurityError(
                "URL resolves to a private or reserved address"
            )
    return host


@dataclass(frozen=True, slots=True)
class NetworkResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "headers": self.headers,
            "body": self.body.decode("utf-8", errors="replace"),
        }

    def as_model_dict(
        self,
        *,
        max_body_bytes: int = MODEL_RESPONSE_BODY_MAX_BYTES,
    ) -> dict[str, Any]:
        """返回仅供模型消费的有界响应，不改变原始响应序列化。"""
        is_html = _looks_like_html(self.headers, self.body)
        decoded = (
            _extract_readable_html(self.body)
            if is_html
            else self.body.decode("utf-8", errors="replace")
        )
        body, rendered_truncated = _truncate_utf8(decoded, max_body_bytes)
        original_body_bytes = len(self.body)
        return {
            "status": self.status,
            "headers": {
                key: value
                for key, value in self.headers.items()
                if key.lower() in _MODEL_RESPONSE_HEADERS
            },
            "body": body,
            "body_format": "html_text" if is_html else "text",
            "truncated": (
                rendered_truncated or original_body_bytes > max_body_bytes
            ),
            "original_body_bytes": original_body_bytes,
        }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


@dataclass(slots=True)
class NetworkBroker:
    timeout_seconds: int = 20
    max_response_bytes: int = 1_000_000
    max_redirects: int = 3

    def request(
        self,
        method: str,
        url: str,
        *,
        allowlist: list[str] | tuple[str, ...],
        allow_public_read: bool = False,
        headers: dict[str, str] | None = None,
        body: str | bytes | None = None,
    ) -> NetworkResponse:
        method = method.upper().strip()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
            raise NetworkSecurityError("HTTP method is not allowed")
        public_read = allow_public_read and method in {"GET", "HEAD"}
        if public_read and body is not None:
            raise NetworkSecurityError("public web reads cannot include a request body")
        current = url
        for _ in range(self.max_redirects + 1):
            host = self._validate_url(
                current,
                allowlist,
                allow_public_read=public_read,
            )
            response = self._request_once(method, current, headers or {}, body)
            if response["status"] not in {301, 302, 303, 307, 308}:
                if len(response["body"]) > self.max_response_bytes:
                    raise NetworkSecurityError("response exceeds the configured limit")
                return NetworkResponse(**response)
            location = response["headers"].get("location") or response["headers"].get("Location")
            if not location:
                raise NetworkSecurityError("redirect has no Location header")
            current = urljoin(current, location)
            if method == "POST" and response["status"] == 303:
                method = "GET"
            # Revalidate the redirected host on the next loop iteration.
            _ = host
        raise NetworkSecurityError("too many redirects")

    def _validate_url(
        self,
        url: str,
        allowlist: list[str] | tuple[str, ...],
        *,
        allow_public_read: bool = False,
    ) -> str:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise NetworkSecurityError("only HTTP(S) URLs are allowed")
        if parsed.username or parsed.password:
            raise NetworkSecurityError("URL credentials are not allowed")
        if not parsed.hostname:
            raise NetworkSecurityError("URL host is missing")
        host = parsed.hostname.rstrip(".").lower()
        if not allow_public_read and not self._allowed_host(host, allowlist):
            raise NetworkSecurityError(
                "URL host is not on the configured business API allowlist; "
                "http_request is not a general web-search tool and arbitrary "
                "host retries are not allowed; use a configured search capability "
                "or search_skill_catalog for Skill discovery"
            )
        reject_private_host(host, parsed.port)
        return host

    @staticmethod
    def _allowed_host(host: str, allowlist: list[str] | tuple[str, ...]) -> bool:
        for raw in allowlist:
            candidate = raw.strip().rstrip(".").lower()
            if candidate.startswith("*."):
                if host.endswith(candidate[1:]) and host != candidate[2:]:
                    return True
            elif host == candidate:
                return True
        return False

    @staticmethod
    def _resolve_addresses(host: str, port: int | None) -> list[str]:
        return _resolve_addresses(host, port)

    def _request_once(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: str | bytes | None,
    ) -> dict[str, Any]:
        forbidden = {"host", "content-length", "proxy", "proxy-authorization"}
        safe_headers = {
            key: value
            for key, value in headers.items()
            if key.lower() not in forbidden
        }
        payload = body.encode("utf-8") if isinstance(body, str) else body
        request = Request(url, data=payload, headers=safe_headers, method=method)
        opener = build_opener(ProxyHandler({}), _NoRedirect)
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                return {
                    "status": int(response.getcode()),
                    "headers": {key: value for key, value in response.headers.items()},
                    "body": response.read(self.max_response_bytes + 1),
                }
        except HTTPError as exc:
            return {
                "status": int(exc.code),
                "headers": {key: value for key, value in exc.headers.items()},
                "body": exc.read(self.max_response_bytes + 1),
            }
        except OSError as exc:
            raise NetworkSecurityError("network request failed") from exc
