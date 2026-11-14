from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import new_token, now
from app.models.quality import RagEvaluationRun


def create_evaluation_run(
    db: Session,
    *,
    dataset_version: str,
    code_revision: str,
    mode: str,
    status: str = "running",
    metrics: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    started_at: datetime | None = None,
) -> RagEvaluationRun:
    run = RagEvaluationRun(
        id=f"rge_{new_token(10)}",
        dataset_version=dataset_version[:128],
        code_revision=code_revision[:128],
        mode=mode[:32],
        status=status,
        metrics_json=json.dumps(metrics or {}, ensure_ascii=False, separators=(",", ":")),
        config_json=json.dumps(config or {}, ensure_ascii=False, separators=(",", ":")),
        started_at=started_at or now(),
        completed_at=now() if status != "running" else None,
    )
    db.add(run)
    db.flush()
    return run


def complete_evaluation_run(
    db: Session,
    run: RagEvaluationRun,
    *,
    status: str,
    metrics: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> RagEvaluationRun:
    run.status = status
    run.metrics_json = json.dumps(metrics, ensure_ascii=False, separators=(",", ":"))
    if config is not None:
        run.config_json = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
    run.completed_at = now()
    db.flush()
    return run
