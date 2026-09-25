#!/usr/bin/env python3
"""Regenerate Dockerfile.legacy from Dockerfile, or check that it is current.

Dockerfile.legacy exists because the deploy host builds with the classic
builder, which cannot parse the `RUN --mount=type=cache` in the main
Dockerfile; scripts/docker_compose_build.sh falls back to it on exactly that
error. That makes the legacy file the one that actually builds on the CSE
runners, so drift between the two is not cosmetic: it ships a different image
than the one anybody reviewed.

It had already drifted when this script was written, missing both
`COPY study-control.html` and `ENV PYTHONUNBUFFERED=1`, which between them
would have deployed without the study control panel and with no container
logs at all. Keeping the two in sync by hand is what failed; this does it
mechanically instead.

Usage:
    python scripts/sync_legacy_dockerfile.py            # rewrite Dockerfile.legacy
    python scripts/sync_legacy_dockerfile.py --check    # exit 1 if out of date
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
LEGACY = REPO_ROOT / "Dockerfile.legacy"

SYNTAX_LINE = "# syntax=docker/dockerfile:1\n"

LEGACY_HEADER = """# Fallback for hosts running a Docker version too old for BuildKit cache
# mounts (`RUN --mount=type=cache`, used by the main Dockerfile) -- older
# Docker Engine either doesn't ship BuildKit, or doesn't default to it, and
# fails outright on that RUN syntax. This is byte-for-byte the same image
# except the conda package cache is a plain image layer instead of a cache
# mount, so a rebuild after touching environment.yml re-downloads packages
# instead of reusing a persistent cache.
#
# GENERATED FROM Dockerfile by scripts/sync_legacy_dockerfile.py -- do not edit
# by hand. Run that script after changing Dockerfile, and commit both.
"""

CACHE_BLOCK = """# The package cache is a BuildKit cache mount, not an image layer, so it survives
# across builds without bloating the image — a rebuild after touching
# environment.yml re-solves but does not re-download anything already fetched.
# Nothing to `conda clean` afterward: cleaning would just empty the cache mount
# for no image-size benefit.
COPY environment.yml .
RUN --mount=type=cache,target=/opt/conda/pkgs \\
    conda env create -f environment.yml
"""

PLAIN_BLOCK = """COPY environment.yml .
RUN conda env create -f environment.yml && conda clean -afy
"""


def render_legacy(dockerfile_text: str) -> str:
    """The legacy Dockerfile implied by the current Dockerfile.

    Raises if either substitution target is missing rather than emitting a
    file that silently lost the BuildKit-specific parts it exists to replace.
    """
    if not dockerfile_text.startswith(SYNTAX_LINE):
        raise SystemExit(
            "Dockerfile no longer starts with the BuildKit syntax directive; "
            "update scripts/sync_legacy_dockerfile.py to match."
        )
    if CACHE_BLOCK not in dockerfile_text:
        raise SystemExit(
            "Could not find the conda cache-mount block in Dockerfile verbatim; "
            "update scripts/sync_legacy_dockerfile.py to match."
        )

    out = dockerfile_text.replace(SYNTAX_LINE, LEGACY_HEADER, 1)
    return out.replace(CACHE_BLOCK, PLAIN_BLOCK, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if Dockerfile.legacy is out of date, changing nothing.",
    )
    args = parser.parse_args()

    expected = render_legacy(DOCKERFILE.read_text())
    current = LEGACY.read_text() if LEGACY.exists() else None

    if args.check:
        if current == expected:
            print("Dockerfile.legacy is up to date.")
            return 0
        print(
            "Dockerfile.legacy is out of date with Dockerfile.\n"
            "Run: python scripts/sync_legacy_dockerfile.py",
            file=sys.stderr,
        )
        return 1

    if current == expected:
        print("Dockerfile.legacy already up to date.")
        return 0

    LEGACY.write_text(expected)
    print(f"Wrote {LEGACY.relative_to(REPO_ROOT)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
