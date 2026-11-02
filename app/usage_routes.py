"""/usage and /usage.json: the usage report, behind a password.

The password's scrypt hash comes from ``CAD_A11Y_USAGE_PASSWORD_HASH``, made by
``python -m app.usage_report hash``. With no hash, or one that is not well
formed, both routes answer 404 as if they did not exist: the report is off
until someone turns it on, never open because a setting was forgotten.

The browser's own Basic auth prompt asks for the password, so there is no
sign-in page, cookie or session to build, and the prompt is one screen readers
already know. The username is ignored. The check is ``tokens.verify``, the one
the study panels use, with its cache of passwords that have verified and its
limit on how many checks run at once.

Not registered on a demo station, and closed to demo requests on any other
server (``_CLOSED_TO_DEMO`` in server.py).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from flask import Blueprint, Response, abort, request

from . import db, usage_report
from .studies import tokens

HASH_ENV = "CAD_A11Y_USAGE_PASSWORD_HASH"

_CHALLENGE = 'Basic realm="CAD A11y usage", charset="UTF-8"'
_HEADERS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}

usage_bp = Blueprint("usage", __name__)

_builtin_dir: Callable[[], Path] = (
    lambda: Path(__file__).resolve().parent.parent / "builtin_models"
)


def configure(*, builtin_dir: Callable[[], Path]) -> None:
    """Where the built-in models are, so a selected model can be named only when it is one."""
    global _builtin_dir
    _builtin_dir = builtin_dir


def _password_hash() -> str | None:
    value = os.environ.get(HASH_ENV, "").strip()
    return value if tokens.is_well_formed(value) else None


def start_message() -> str:
    if _password_hash():
        return "Usage report: on, at /usage."
    return f"Usage report: off. Set {HASH_ENV} to a hash from `python -m app.usage_report hash` to turn it on."


@usage_bp.before_request
def _require_password():
    expected = _password_hash()
    if expected is None:
        abort(404)
    credentials = request.authorization
    password = (
        credentials.password
        if credentials is not None and credentials.type == "basic"
        else None
    )
    if not tokens.verify(password, expected):
        return Response(
            "Authentication required.\n",
            401,
            {"WWW-Authenticate": _CHALLENGE},
            mimetype="text/plain",
        )
    return None


@usage_bp.after_request
def _protect(response: Response) -> Response:
    response.headers.update(_HEADERS)
    return response


def _builtin_names() -> frozenset[str]:
    folder = _builtin_dir()
    if not folder.is_dir():
        return frozenset()
    return frozenset(
        name
        for path in folder.iterdir()
        if path.is_file()
        for name in (path.name, path.stem)
    )


def _report() -> usage_report.Report:
    # A read, not a recording: build_report opens its own read-only connection
    # to the file, so it goes around the recorder the way no write may.
    return usage_report.build_report(
        db.DB_PATH,
        exclude_workshop=request.args.get("workshop") == "exclude",
        builtin_names=_builtin_names(),
    )


@usage_bp.get("/usage")
def usage_page() -> Response:
    return Response(usage_report.render_html(_report()), mimetype="text/html")


@usage_bp.get("/usage.json")
def usage_json() -> Response:
    return Response(
        json.dumps(usage_report.report_to_json(_report())), mimetype="application/json"
    )
