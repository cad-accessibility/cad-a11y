"""Checking an email address before it is kept.

One function, used by both ``/session/identify`` and ``db.add_contact``, so an
address cannot reach the contacts list by a route that checks less. The old
check was ``^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$``, which accepted any text without a
space or a second @: a megabyte of it, control characters, markup, doubled dots,
and a JSON list turned into text (#243 review).

What it accepts is an address a person could type and receive mail at, in any
script. ``josé@example.es`` and ``ana@münchen.de`` are addresses, and refusing
them would turn away people whose names are not ASCII. What it refuses:

* anything that is not text;
* more than 254 bytes in all, or 64 before the @;
* a local part with anything but letters, digits and ``.!#$%&'*+/=?^_`{|}~-``,
  or a leading, trailing or doubled dot;
* a domain that is not at least two labels of 1 to 63 letters, digits and
  hyphens, with no label starting or ending in a hyphen, and a last label that is
  at least two characters and not all digits.

The address comes back stripped, in NFC, with its domain lowercased, so the same
address typed twice is stored once.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

MAX_BYTES = 254
MAX_LOCAL_BYTES = 64

# [^\W_] is a letter or digit in any script: \w less the underscore.
_ATOM = r"(?:[^\W_]|[!#$%&'*+/=?^_`{|}~-])+"
_LOCAL = rf"{_ATOM}(?:\.{_ATOM})*"
_LABEL = r"[^\W_](?:(?:[^\W_]|-){0,61}[^\W_])?"
_ADDRESS = re.compile(rf"(?P<local>{_LOCAL})@(?P<domain>{_LABEL}(?:\.{_LABEL})+)")


class InvalidEmail(ValueError):
    """Not an address that can be kept. ``reason`` is ``"too_long"`` or
    ``"invalid"``, so the dialog can say which."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def normalise(raw: Any) -> str:
    """The address to store, or ``InvalidEmail``."""
    if not isinstance(raw, str):
        raise InvalidEmail("invalid")
    # Before anything else looks at it: a megabyte of text is too long however
    # it would have matched, and the pattern need not see it.
    if len(raw) > 4 * MAX_BYTES:
        raise InvalidEmail("too_long")
    text = unicodedata.normalize("NFC", raw.strip())
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise InvalidEmail("too_long")
    match = _ADDRESS.fullmatch(text)
    if not match:
        raise InvalidEmail("invalid")
    local, domain = match.group("local"), match.group("domain")
    if len(local.encode("utf-8")) > MAX_LOCAL_BYTES:
        raise InvalidEmail("too_long")
    last = domain.rsplit(".", 1)[1]
    if len(last) < 2 or last.isdigit():
        raise InvalidEmail("invalid")
    return f"{local}@{domain.lower()}"


def message_for(error: InvalidEmail) -> str:
    """What the dialog says, in words a person can act on."""
    if error.reason == "too_long":
        return "That email address is too long. Addresses can be up to 254 characters."
    return "That doesn't look like an email address. Check it and try again."
