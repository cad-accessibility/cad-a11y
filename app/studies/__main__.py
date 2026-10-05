"""The command line for studies.

    python -m app.studies list
    python -m app.studies token
    python -m app.studies check <slug> [--db FILE] [--logs DIR]
    python -m app.studies export <slug> [--out FILE] [--db FILE] [--logs DIR]

``check`` and ``export`` read a study's data wherever its definition says it is,
or from ``--db`` and ``--logs`` when a copy has been restored somewhere else.
They work on a study of any status, retired included, and never modify what
they read. On a server, run them inside the container:

    docker compose exec app conda run --no-capture-output -n cad-a11y \
        python -m app.studies list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import export, registry, tokens
from .definition import Study
from .definitions import ALL


def _list(_: argparse.Namespace) -> int:
    opened = registry.opened_by_environment()
    for study in ALL:
        storage = study.resolve_storage()
        served = registry.serving_status(study, opened)
        state = study.status.value
        if served is not None:
            refused = registry.refusal(study)
            state += f", not served: {refused}" if refused else f", served {served.value}"
        print(f"{study.slug}  ({state})")
        print(f"    {study.title}, version {study.version}")
        print(f"    database {storage.db_path}{'' if storage.db_path.is_file() else ' (none yet)'}")
        print(f"    logs     {storage.log_dir}")
    return 0


def _token(_: argparse.Namespace) -> int:
    token = tokens.generate()
    print("Panel token (give it to the experimenters privately; it is not stored anywhere):")
    print(f"    {token}")
    print()
    print("Its hash, for the study's definition (token_hash=...):")
    print(f"    {tokens.hash_token(token)}")
    return 0


def _paths(args: argparse.Namespace) -> tuple[Study, Path, Path] | None:
    study = registry.find_definition(args.slug)
    if study is None:
        print(f"No study called {args.slug}. `python -m app.studies list` shows them.", file=sys.stderr)
        return None
    storage = study.resolve_storage()
    db_path = Path(args.db) if args.db else storage.db_path
    log_dir = Path(args.logs) if args.logs else storage.log_dir
    if not db_path.is_file():
        print(f"No database at {db_path}.", file=sys.stderr)
        return None
    return study, db_path, log_dir


def _check(args: argparse.Namespace) -> int:
    found = _paths(args)
    if found is None:
        return 2
    _, db_path, log_dir = found
    with export.readable_copy(db_path, log_dir) as (_, store):
        report = export.run_checks(store, log_dir)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(export.summarise_checks(report))
    return 1 if report["problems"] else 0


def _export(args: argparse.Namespace) -> int:
    found = _paths(args)
    if found is None:
        return 2
    study, db_path, log_dir = found
    out = Path(args.out) if args.out else Path(f"{args.slug}_{export.timestamp_for_filename()}.zip")
    if out.exists():
        print(f"{out} already exists; choose another --out.", file=sys.stderr)
        return 2
    with out.open("xb") as handle:
        export.build_archive(study, db_path, log_dir, target=handle)
    print(f"Wrote {out}")
    with export.readable_copy(db_path, log_dir) as (_, store):
        print(export.summarise_checks(export.run_checks(store, log_dir)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.studies", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="every study, its status and where its data is").set_defaults(run=_list)
    commands.add_parser("token", help="make a panel token and the hash for a definition").set_defaults(run=_token)

    for name, run, summary in (
        ("check", _check, "look for known problems in a study's data"),
        ("export", _export, "write a study's data and its codebooks to a zip"),
    ):
        command = commands.add_parser(name, help=summary)
        command.add_argument("slug")
        command.add_argument("--db", help="a database to read instead of the study's own")
        command.add_argument("--logs", help="a log directory to read instead of the study's own")
        if name == "export":
            command.add_argument("--out", help="the zip to write; refuses to overwrite")
        else:
            command.add_argument("--json", action="store_true", help="print the report as JSON")
        command.set_defaults(run=run)

    args = parser.parse_args(argv)
    return int(args.run(args))


if __name__ == "__main__":
    sys.exit(main())
