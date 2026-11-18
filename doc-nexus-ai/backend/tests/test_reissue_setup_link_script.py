from __future__ import annotations

import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "reissue-setup-link.sh"


def test_reissue_setup_link_is_retired_without_side_effects(tmp_path: Path) -> None:
    root = tmp_path / "deployment"
    root.mkdir()
    env_file = root / ".env"
    original_env = b"DOC_NEXUS_BOOTSTRAP_PASSWORD=old-secret\n"
    env_file.write_bytes(original_env)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    docker = fake_bin / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$DOCKER_LOG"\nexit 0\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)

    completed = subprocess.run(
        ["bash", str(SCRIPT), "--target", "test"],
        env={
            **os.environ,
            "DOC_NEXUS_ROOT_DIR": str(root),
            "DOCKER_LOG": str(docker_log),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "Setup ticket mechanism has been removed." in completed.stderr
    assert "Access /setup directly" in completed.stderr
    assert completed.stdout == ""
    assert "old-secret" not in completed.stderr
    assert env_file.read_bytes() == original_env
    assert not docker_log.exists()
    assert not list(root.glob(".env.setup-link-backup.*"))
