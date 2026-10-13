"""Which release of the app is running, for the record each session keeps."""

from __future__ import annotations

import functools
import os
import subprocess

from .definition import repo_root


@functools.lru_cache(maxsize=1)
def app_version() -> str:
    """The deployed tag or commit, or ``unknown``.

    The image is built without ``.git``, so on a server this comes from
    ``CAD_A11Y_VERSION``, which ``scripts/docker_compose_build.sh`` sets from the
    pipeline's tag or commit. In a checkout it asks git.
    """
    configured = os.environ.get("CAD_A11Y_VERSION", "").strip()
    if configured:
        return configured
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=repo_root(),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"
