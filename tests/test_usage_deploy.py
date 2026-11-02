"""How the usage report's password gets from GitHub to the running container.

The password is the GitHub Actions secret CAD_A11Y_USAGE_PASSWORD. The mirror
workflow hashes it with ``python -m app.usage_report hash --stdin`` and hands the
hash, base64-encoded, to the GitLab deploy pipeline as a push option.
scripts/docker_compose_build.sh decodes it, and docker-compose.yml passes it into
the container. These tests run each hop for real.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from app.studies import tokens

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct horse battery staple"
# A salt that starts with a letter, so "$aB..." reads as a variable reference to
# anything that interpolates it.
HASH = tokens.hash_token(PASSWORD, salt=b"\x68" * 16)


def test_hash_from_stdin_prints_a_hash_of_the_password():
    result = subprocess.run(
        [sys.executable, "-m", "app.usage_report", "hash", "--stdin"],
        cwd=ROOT,
        input=PASSWORD,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    printed = result.stdout.strip()
    assert PASSWORD not in result.stdout
    assert tokens.verify(PASSWORD, printed)


def test_hash_from_stdin_refuses_an_empty_password():
    result = subprocess.run(
        [sys.executable, "-m", "app.usage_report", "hash", "--stdin"],
        cwd=ROOT,
        input="",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""


def _run_build_script(tmp_path: Path, env: dict[str, str]) -> str | None:
    """Run the build script with a fake docker that records the hash it was given."""
    seen = tmp_path / "seen"
    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir()
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'if [ -n "${CAD_A11Y_USAGE_PASSWORD_HASH+set}" ]; then\n'
        f'  printf "%s" "$CAD_A11Y_USAGE_PASSWORD_HASH" > "{seen}"\n'
        "fi\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    base = {k: v for k, v in os.environ.items() if not k.startswith("CAD_A11Y_USAGE")}
    result = subprocess.run(
        ["bash", "scripts/docker_compose_build.sh"],
        cwd=ROOT,
        env={
            **base,
            **env,
            "PATH": f"{fake.parent}:{base['PATH']}",
            "CAD_A11Y_VERSION": "test",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return seen.read_text() if seen.exists() else None


def test_the_build_script_hands_compose_the_decoded_hash(tmp_path):
    encoded = base64.b64encode(HASH.encode()).decode()

    seen = _run_build_script(tmp_path, {"CAD_A11Y_USAGE_PASSWORD_HASH_B64": encoded})

    assert seen == HASH


def test_without_the_pipeline_variable_the_build_script_sets_no_hash(tmp_path):
    assert _run_build_script(tmp_path, {}) is None


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker compose")
def test_compose_passes_the_hash_into_the_container_as_written():
    base = {k: v for k, v in os.environ.items() if not k.startswith("CAD_A11Y_USAGE")}
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "--env-file",
            "/dev/null",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env={**base, "CAD_A11Y_USAGE_PASSWORD_HASH": HASH},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    environment = json.loads(result.stdout)["services"]["app"]["environment"]
    # config prints each literal "$" as "$$" so its output reads back the same;
    # a value Compose had expanded would have lost the "$aGho..." part instead.
    assert environment["CAD_A11Y_USAGE_PASSWORD_HASH"].replace("$$", "$") == HASH


@pytest.mark.parametrize(
    "piped, password",
    [
        (PASSWORD + "\n", PASSWORD),
        (PASSWORD + "\r\n", PASSWORD),
        (" pw \r\n", " pw "),
        ("pw\n\n", "pw\n"),
    ],
)
def test_hash_from_stdin_drops_one_line_ending(piped, password):
    # A secret stored from `echo password | ...` ends in a line ending that no
    # browser's password prompt can send.
    result = subprocess.run(
        [sys.executable, "-m", "app.usage_report", "hash", "--stdin"],
        cwd=ROOT,
        input=piped.encode(),
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert tokens.verify(password, result.stdout.decode().strip())


@pytest.mark.parametrize("piped", ["\n", "\r\n"])
def test_hash_from_stdin_refuses_a_bare_line_ending(piped):
    result = subprocess.run(
        [sys.executable, "-m", "app.usage_report", "hash", "--stdin"],
        cwd=ROOT,
        input=piped.encode(),
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == b""
