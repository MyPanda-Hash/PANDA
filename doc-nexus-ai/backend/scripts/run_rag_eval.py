#!/usr/bin/env python3
"""Run a privacy-safe legacy/auto retrieval comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_repo_root(), text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _dataset_version(path: Path, records: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"{path.stem}:{digest}:{len(records)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--modes", default="legacy,auto")
    parser.add_argument("--output")
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args(argv)

    backend = _repo_root() / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from app.services.rag_evaluation import evaluate_modes, load_dataset

    dataset_path = Path(args.dataset).expanduser().resolve()
    records = load_dataset(dataset_path)
    manifest: dict[str, Any] = {}
    if args.manifest:
        manifest_value = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        if isinstance(manifest_value, dict):
            manifest = manifest_value

    def retrieve(case: dict[str, Any], mode: str) -> dict[str, Any]:
        configured = (
            manifest.get("cases", {})
            .get(case["id"], {})
            .get(mode)
            if isinstance(manifest.get("cases"), dict)
            else None
        )
        if isinstance(configured, dict):
            return {
                key: value
                for key, value in configured.items()
                if key in {"ranked_source_refs", "authorized_source_refs", "latency_ms", "error"}
            }
        return {"ranked_source_refs": case.get("ranked_source_refs", [])}

    report = evaluate_modes(records, retrieve)
    report.update(
        {
            "dataset_version": _dataset_version(dataset_path, records),
            "code_revision": _revision(),
            "modes": [item.strip() for item in args.modes.split(",") if item.strip()],
            "retrieval_config": {"k": 10},
        }
    )
    report["cases"] = {
        mode: [
            {
                "id": row["id"],
                "ranked_source_refs": row.get("ranked_source_refs", []),
                "latency_ms": row.get("latency_ms"),
                "error": row.get("error"),
            }
            for row in rows
        ]
        for mode, rows in report.get("cases", {}).items()
    }
    output = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)

    if args.persist:
        from app.core.database import transaction
        from app.repositories.quality import create_evaluation_run

        with transaction() as db:
            decision = report["release_decision"]["release_decision"]
            create_evaluation_run(
                db,
                dataset_version=report["dataset_version"],
                code_revision=report["code_revision"],
                mode=",".join(report["modes"]),
                status="passed" if decision == "eligible" else "failed",
                metrics=report["metrics"],
                config=report["retrieval_config"],
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
