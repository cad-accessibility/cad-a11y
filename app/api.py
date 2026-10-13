"""The integration API: a versioned home for tools that send models in (#236).

Everything here lives under /api/v1 and is described by openapi.json beside
this file, which is served at /api/v1/openapi.json. The description is public
on purpose: a tool has to be able to integrate by reading it alone. A test
checks it against the routing table, so a route cannot be added without being
described, or described without existing.

Nothing is in it yet. Tools still send models to /ingest, and #236 moves that
here as a proper API, with keys, limits and models that belong to someone. What
this sets now is the address and the one shape every error takes.

The viewer's own routes (/render, /get_data, /events and the rest) are not part
of it and are not described anywhere public. They are not a contract and change
with the viewer. Maintainers can list every route with `flask --app app.server
routes`.
"""

from __future__ import annotations

import re
from pathlib import Path

from flask import Blueprint, Flask, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

API_PREFIX = "/api/v1"
SPEC_PATH = Path(__file__).resolve().parent / "openapi.json"

api_bp = Blueprint("api", __name__, url_prefix=API_PREFIX)


@api_bp.get("/openapi.json")
def openapi_spec():
    return send_file(SPEC_PATH, mimetype="application/json")


def _is_api_request() -> bool:
    return request.path == "/api" or request.path.startswith("/api/")


def _error_code(error: HTTPException) -> str:
    """A stable, machine-readable name for the error: Not Found is not_found."""
    return re.sub(r"[^a-z0-9]+", "_", (error.name or "error").lower()).strip("_")


def json_errors_under_api(error: HTTPException):
    """Answer every error under /api in one JSON shape, the Error schema in
    openapi.json. Everywhere else the error is returned untouched.

    Registered on the app rather than the blueprint, because a route that does
    not exist fails before any blueprint is chosen.
    """
    if not _is_api_request():
        return error
    response = jsonify(
        {"status": "error", "code": _error_code(error), "message": error.description}
    )
    response.status_code = error.code or 500
    valid_methods = getattr(error, "valid_methods", None)
    if valid_methods:
        response.headers["Allow"] = ", ".join(valid_methods)
    return response


def install(app: Flask) -> None:
    app.register_blueprint(api_bp)
    app.register_error_handler(HTTPException, json_errors_under_api)
