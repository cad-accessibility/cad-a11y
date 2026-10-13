"""Panel tokens: what an experimenter types to open a study's control panel.

Each study's definition carries a hash of its token, never the token itself. The
definitions live in a public repository, so the hash has to be safe to publish:
scrypt, salted, at a cost that makes guessing slow even for a token someone chose
by hand rather than generated. ``python -m app.studies token`` generates one.

Why a token per study rather than one for the deployment
--------------------------------------------------------
The comparison study ran with one switch, ``STUDY_CONTROL_TOKEN``, which neither
server ever set, so its panel was open to anyone who found the address. A token
that lives with the study cannot be forgotten in a server's environment: a study
whose definition has no hash is not served at all (see ``registry``), and closing
or retiring the study takes the token out of use with it.

Verifying costs tens of milliseconds by design, and the panel makes a request on
every action. A token that has verified once is remembered for this process, so
only the first request pays. Failed attempts are not remembered and each one
pays in full, which is the point.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading

# 2**15 is about 40 ms here. OWASP's floor for scrypt is 2**17 for passwords,
# which people choose badly; these are 32 random bytes from ``generate`` and the
# cost only has to cover a token someone typed in themselves.
_N = 2**15
_R = 8
_P = 1
_DKLEN = 32
# scrypt needs 128 * r * n bytes, 32 MiB at these settings; OpenSSL's default
# ceiling is 32 MiB exactly, so it is raised rather than left to fail on a build
# that counts its own overhead against it.
_MAXMEM = 64 * 1024 * 1024
_PREFIX = "scrypt"

_verified: dict[tuple[str, str], bool] = {}
_verified_lock = threading.Lock()
_VERIFIED_LIMIT = 256

# Each check holds 32 MiB while it runs, and the sign-in form is public, so a
# burst of attempts could otherwise take that many times over. Two at a time is
# more than an experimenter signing in will ever notice.
_checking = threading.BoundedSemaphore(2)


def generate() -> str:
    """A new panel token: 32 random bytes, URL-safe, nothing to pad or escape."""
    return secrets.token_urlsafe(32)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_token(token: str, *, salt: bytes | None = None) -> str:
    """The string a study definition stores: ``scrypt$n$r$p$salt$key``."""
    if not token:
        raise ValueError("an empty token cannot be hashed")
    salt = salt if salt is not None else secrets.token_bytes(16)
    key = hashlib.scrypt(
        token.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM, dklen=_DKLEN
    )
    return f"{_PREFIX}${_N}${_R}${_P}${_b64(salt)}${_b64(key)}"


def is_well_formed(token_hash: str | None) -> bool:
    """Whether a stored hash can be checked at all. A typo in a definition is
    caught when the registry loads it, not by a panel that refuses everyone."""
    try:
        _parse(token_hash or "")
    except ValueError:
        return False
    return True


def _parse(token_hash: str) -> tuple[int, int, int, bytes, bytes]:
    parts = token_hash.split("$")
    if len(parts) != 6 or parts[0] != _PREFIX:
        raise ValueError("not a scrypt token hash")
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, key = _unb64(parts[4]), _unb64(parts[5])
    except (ValueError, TypeError) as error:
        raise ValueError(f"malformed token hash: {error}") from error
    if n < 2 or n & (n - 1) or r < 1 or p < 1 or not salt or len(key) != _DKLEN:
        raise ValueError("token hash parameters are out of range")
    return n, r, p, salt, key


def verify(token: str | None, token_hash: str | None) -> bool:
    """Whether ``token`` is the one ``token_hash`` was made from.

    False for anything missing or malformed, never an exception: a panel request
    with a bad header is a refused request, not a 500.
    """
    if not token or not token_hash:
        return False
    # Keyed by a digest of the token rather than the token, so this cache holds
    # no secret a memory dump could read back.
    cache_key = (hashlib.sha256(token.encode("utf-8")).hexdigest(), token_hash)
    with _verified_lock:
        if _verified.get(cache_key):
            return True
    try:
        n, r, p, salt, expected = _parse(token_hash)
        with _checking:
            actual = hashlib.scrypt(
                token.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=len(expected)
            )
    except (ValueError, MemoryError):
        return False
    ok = hmac.compare_digest(actual, expected)
    if ok:
        with _verified_lock:
            if len(_verified) >= _VERIFIED_LIMIT:
                _verified.clear()
            _verified[cache_key] = True
    return ok


def forget_verified() -> None:
    """Drop every remembered verification. For tests."""
    with _verified_lock:
        _verified.clear()
