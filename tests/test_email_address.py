"""The one check an email address passes before it is kept (app/email_address.py).

The cases are the ones from the review of #243: what the old pattern accepted that
it should not have, and the addresses in other scripts that must still get in.
"""

from __future__ import annotations

import pytest

from app.email_address import InvalidEmail, normalise


@pytest.mark.parametrize(
    "raw, kept",
    [
        ("a@example.com", "a@example.com"),
        ("first.last+tag@example.co.uk", "first.last+tag@example.co.uk"),
        ("x@mail.example.org", "x@mail.example.org"),
        ("o'brien@example.ie", "o'brien@example.ie"),
        ("josé@example.es", "josé@example.es"),
        ("ana@münchen.de", "ana@münchen.de"),
        ("ana@xn--mnchen-3ya.de", "ana@xn--mnchen-3ya.de"),
        ("  Bob@Example.COM  ", "Bob@example.com"),
        # Composed and decomposed é are one address.
        ("josé@example.es", "josé@example.es"),
        ("a" * 64 + "@example.com", "a" * 64 + "@example.com"),
    ],
)
def test_an_address_is_kept_and_tidied(raw, kept):
    assert normalise(raw) == kept


@pytest.mark.parametrize(
    "raw, reason",
    [
        ("x" * 1_000_000, "too_long"),
        ("x" * 10_000 + "@example.com", "too_long"),
        ("a" * 65 + "@example.com", "too_long"),
        ("a@" + ".".join(["b" * 60] * 5) + ".com", "too_long"),
        ("a\x00b@example.com", "invalid"),
        ("a\x1bb@example.com", "invalid"),
        ("<script>alert(1)</script>@x.y", "invalid"),
        ("a..b@c.de", "invalid"),
        (".a@c.de", "invalid"),
        ("a.@c.de", "invalid"),
        ("a@b..c", "invalid"),
        ("a@.b.cd", "invalid"),
        ("a@b.cd.", "invalid"),
        ("a@b.1", "invalid"),
        ("a@b.c", "invalid"),
        ("a@localhost", "invalid"),
        ("a@-b.com", "invalid"),
        ("a@b-.com", "invalid"),
        ("a@b_c.de", "invalid"),
        ("a@" + "x" * 64 + ".com", "invalid"),
        ("a b@c.de", "invalid"),
        ("a@@b.cd", "invalid"),
        ("no-at-sign", "invalid"),
        ('"quoted"@example.com', "invalid"),
        ("", "invalid"),
        ("   ", "invalid"),
        (["a@b.cd"], "invalid"),
        ({"email": "a@b.cd"}, "invalid"),
        (123, "invalid"),
        (None, "invalid"),
    ],
)
def test_anything_else_is_refused_and_says_why(raw, reason):
    with pytest.raises(InvalidEmail) as caught:
        normalise(raw)
    assert caught.value.reason == reason
