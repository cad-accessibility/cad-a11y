#!/usr/bin/env python3
"""Remove accumulated non-built-in files from the built-in model directory.

Until the storage split, uploads and workshop /ingest models were written into the
same directory as the built-in models, so on a long-lived deployment every
participant file accumulated there and was served to every visitor. This reclaims
the directory.

The rule is "anything in MODEL_DIR that is not in builtin_models/". Matching on a
name pattern such as ``ingest_*`` does not work: /ingest names files from the
caller's ``?filename=`` or the uploaded filename, so on a real server participant
files are named after the participant's own model, not after the endpoint. The
only dependable statement is that MODEL_DIR should contain exactly the tracked
built-ins, because the server now seeds it from builtin_models/ on every start and
writes uploads to UPLOAD_DIR instead.

Defaults to a dry run; pass --apply to delete.

    python scripts/cleanup_ingest_models.py            # report only
    python scripts/cleanup_ingest_models.py --apply    # delete

Inside the container the directories are /project/data/models and
/project/builtin_models.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path

MODEL_SUFFIXES = {".stl", ".step"}
REPO_ROOT = Path(__file__).resolve().parent.parent


def find_stale_models(model_dir: Path, builtin_dir: Path) -> list[Path]:
    """Return model files in model_dir that are not shipped built-ins."""
    if not model_dir.is_dir():
        return []
    builtin_names = (
        {path.name for path in builtin_dir.iterdir() if path.is_file()}
        if builtin_dir.is_dir()
        else set()
    )
    return sorted(
        path
        for path in model_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in MODEL_SUFFIXES
        and path.name not in builtin_names
    )


def find_models_matching_patterns(
    directories: list[Path], patterns: list[str], builtin_names: set[str]
) -> list[Path]:
    """Return model files in any of `directories` whose name matches a pattern.

    For a one-time cleanup of a known-junk naming convention (an ingest tool's
    output, a batch-test prefix, etc.) rather than the "anything not a shipped
    built-in" rule above — that rule doesn't apply to upload_dir, where every
    file is legitimately not a built-in. `builtin_names` is still excluded here
    as a safety net, so a pattern can never delete a shipped built-in even if
    it happens to match one by coincidence.

    Patterns are matched against the filename (not the full path) using shell
    globbing (`fnmatch`), e.g. "layered_tag_*" — case-sensitive, matching
    normal Linux filesystem behaviour on the servers this runs on.
    """
    matches: list[Path] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix.lower() not in MODEL_SUFFIXES:
                continue
            if path.name in builtin_names:
                continue
            if any(fnmatch.fnmatch(path.name, pattern) for pattern in patterns):
                matches.append(path)
    return matches


def find_duplicate_stems(model_dir: Path, upload_dir: Path) -> dict[str, list[Path]]:
    """Return stems used by more than one model file across both directories.

    A model is named to the client by its stem, so a duplicate makes one of the two
    unreachable. New uploads are renamed to avoid this, but files predating the
    storage split can still collide, and only a person can decide which to keep.
    """
    by_stem: dict[str, list[Path]] = {}
    for directory in (model_dir, upload_dir):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.suffix.lower() in MODEL_SUFFIXES:
                by_stem.setdefault(path.stem, []).append(path)
    return {stem: paths for stem, paths in by_stem.items() if len(paths) > 1}


def _report_and_maybe_delete(targets: list[Path], *, apply: bool, where: str) -> int:
    """Shared dry-run/delete/report body for both cleanup modes below."""
    if not targets:
        print(f"Nothing to do in {where}.")
        return 0

    total_bytes = sum(path.stat().st_size for path in targets)
    print(f"{len(targets)} model(s) in {where} ({total_bytes / 1e6:.1f} MB)")

    if not apply:
        for path in targets[:10]:
            print(f"  would delete {path}")
        if len(targets) > 10:
            print(f"  ... and {len(targets) - 10} more")
        print("\nDry run. Re-run with --apply to delete.")
        return 0

    deleted = 0
    for path in targets:
        try:
            path.unlink()
            deleted += 1
        except OSError as error:
            print(f"  could not delete {path}: {error}", file=sys.stderr)
    print(f"Deleted {deleted} of {len(targets)} file(s)")
    return 0 if deleted == len(targets) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", default=str(REPO_ROOT / "data" / "models"))
    parser.add_argument("--builtin-dir", default=str(REPO_ROOT / "builtin_models"))
    parser.add_argument("--upload-dir", default=str(REPO_ROOT / "data" / "uploads"))
    parser.add_argument(
        "--pattern",
        action="append",
        default=None,
        metavar="GLOB",
        help=(
            "One-time cleanup mode: delete files matching this shell-style glob "
            "(e.g. 'layered_tag_*'), by name, in both --model-dir and --upload-dir. "
            "Repeatable. Replaces the default 'anything not a shipped built-in' "
            "check above, since that rule only makes sense for --model-dir — "
            "every file in --upload-dir is legitimately not a built-in."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Without this the script only reports.",
    )
    args = parser.parse_args(argv)

    model_dir = Path(args.model_dir)
    builtin_dir = Path(args.builtin_dir)
    upload_dir = Path(args.upload_dir)

    if not builtin_dir.is_dir():
        print(
            f"Built-in directory {builtin_dir} not found; refusing to run, since "
            "every model would look stale.",
            file=sys.stderr,
        )
        return 2

    if args.pattern:
        builtin_names = {path.name for path in builtin_dir.iterdir() if path.is_file()}
        targets = find_models_matching_patterns([model_dir, upload_dir], args.pattern, builtin_names)
        where = f"{model_dir} and {upload_dir} matching {args.pattern}"
        return _report_and_maybe_delete(targets, apply=args.apply, where=where)

    duplicates = find_duplicate_stems(model_dir, upload_dir)
    if duplicates:
        print(f"Warning: {len(duplicates)} stem(s) name more than one model file.")
        print("A stem identifies a model to the client, so one of each pair is")
        print("unreachable. Decide which to keep; this script will not choose.")
        for stem, paths in list(duplicates.items())[:10]:
            print(f"  {stem}: " + ", ".join(str(p) for p in paths))
        if len(duplicates) > 10:
            print(f"  ... and {len(duplicates) - 10} more")
        print()

    targets = find_stale_models(model_dir, builtin_dir)
    return _report_and_maybe_delete(targets, apply=args.apply, where=str(model_dir))


if __name__ == "__main__":
    raise SystemExit(main())
