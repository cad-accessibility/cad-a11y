"""Dockerfile.legacy must stay in sync with Dockerfile.

The deploy host builds with the classic builder, which cannot parse the
``RUN --mount=type=cache`` in the main Dockerfile, so
scripts/docker_compose_build.sh falls back to Dockerfile.legacy on exactly
that error. The legacy file is therefore the one that actually builds on the
CSE runners, and drift between the two ships an image nobody reviewed.

It had already drifted once: the legacy copy was missing both
``COPY study-control.html`` and ``ENV PYTHONUNBUFFERED=1``, which between them
would have deployed without the study control panel and with no container logs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_legacy_dockerfile_is_up_to_date():
    """Fails when Dockerfile changed but Dockerfile.legacy was not regenerated."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "sync_legacy_dockerfile.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"{result.stdout}{result.stderr}\n"
        "Dockerfile.legacy is stale. Run: python scripts/sync_legacy_dockerfile.py"
    )


def test_legacy_dockerfile_has_no_buildkit_only_syntax():
    """The whole point of the fallback is that it parses without BuildKit.

    Instruction lines only: the header comment names the very syntax it exists
    to avoid, so a naive substring check over the whole file fails on prose.
    """
    legacy = (REPO_ROOT / "Dockerfile.legacy").read_text()
    instructions = [
        line for line in legacy.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]

    assert not any("--mount=" in line for line in instructions)
    assert not legacy.startswith("# syntax=")


def test_both_dockerfiles_run_the_ownership_repair_entrypoint():
    """A build that skips the entrypoint reintroduces the unwritable volume.

    Pinned separately from the sync check so that the reason this matters
    survives even if the generation strategy changes.
    """
    for name in ("Dockerfile", "Dockerfile.legacy"):
        text = (REPO_ROOT / name).read_text()

        assert "COPY scripts/docker-entrypoint.sh /usr/local/bin/" in text, name
        assert "/usr/local/bin/docker-entrypoint.sh" in text, name
        # A USER line would make the container unprivileged before the
        # entrypoint could chown the volumes, which is the bug it fixes.
        assert "\nUSER " not in text, name


def test_entrypoint_is_not_excluded_from_the_build_context():
    """scripts/* is pruned, so the entrypoint needs an explicit exception.

    Without it the COPY fails the build outright rather than degrading.
    """
    dockerignore = (REPO_ROOT / ".dockerignore").read_text()

    assert "!scripts/docker-entrypoint.sh" in dockerignore
