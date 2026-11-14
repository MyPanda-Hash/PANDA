from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required Harness environment: {name}")
    return value


def _workspace_relative(path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        workspace = Path(_required_env("HARNESS_WORKSPACE_ROOT")).resolve()
        try:
            candidate = candidate.resolve().relative_to(workspace)
        except ValueError as exc:
            raise ValueError(
                "upload path must stay inside the Harness workspace"
            ) from exc
    if not candidate.parts or ".." in candidate.parts:
        raise ValueError("upload path must be a safe workspace-relative path")
    return candidate.as_posix()


def _gateway_base_url() -> str:
    gateway = _required_env("HARNESS_GATEWAY_URL").rstrip("/")
    parsed = urlsplit(gateway)
    try:
        _ = parsed.port
    except ValueError as exc:
        raise RuntimeError("Harness gateway must be a valid HTTP(S) URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("Harness gateway must be a valid HTTP(S) URL")
    return gateway


def _upload(path: str) -> dict:
    relative = _workspace_relative(path)
    gateway = _gateway_base_url()
    token = _required_env("HARNESS_GATEWAY_TOKEN")
    payload = {
        "task_id": _required_env("HARNESS_TASK_ID"),
        "tenant_id": _required_env("HARNESS_TENANT_ID"),
        "user_id": _required_env("HARNESS_USER_ID"),
        "path": relative,
    }
    request = Request(
        f"{gateway}/admin/harness/files/upload",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    # The gateway URL is restricted to HTTP(S) by _gateway_base_url.
    with urlopen(request, timeout=30) as response:  # nosec B310
        return json.loads(response.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload a Harness workspace file")
    parser.add_argument("path")
    parser.add_argument("--batch", action="store_true")
    args = parser.parse_args()

    target = Path(args.path)
    if args.batch:
        workspace = Path(_required_env("HARNESS_WORKSPACE_ROOT")).resolve()
        directory = (target if target.is_absolute() else workspace / target).resolve()
        try:
            directory.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(
                "batch path must stay inside the Harness workspace"
            ) from exc
        if not directory.is_dir():
            raise ValueError("batch path must be a directory")
        result = {
            "files": [
                _upload(str(path))["file"]
                for path in sorted(directory.rglob("*"))
                if path.is_file()
            ]
        }
    else:
        result = _upload(args.path)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
