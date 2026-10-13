"""The image installs pinned versions, each from one place (#240).

requirements.txt lists what the app depends on. The versions come from two
files: environment.yml pins what conda installs, and requirements-lock.txt pins
what pip installs on top of it. Without pins, every rebuild re-resolved each
package to its newest release, so a new geometry library could change what
someone feels on the display with nothing to show for it but a subtly different
render. And with a package in both, pip replaced conda's copy with its own, as it
did with numpy.

These tests keep the three files honest with each other and every install path
reading the lock.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements-lock.txt"
DIRECT = ROOT / "requirements.txt"
ENVIRONMENT = ROOT / "environment.yml"


def _requirements(path: Path) -> list[Requirement]:
    """Parse a pip requirements file: comments, inline ones included, dropped."""
    requirements = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if line:
            requirements.append(Requirement(line))
    return requirements


def _locked() -> dict[str, str]:
    return {
        canonicalize_name(requirement.name): next(iter(requirement.specifier)).version
        for requirement in _requirements(LOCK)
    }


def _conda_pins() -> dict[str, str | None]:
    """The dependencies in environment.yml, by name, with the version each is
    pinned to, or None. Parsed by hand: PyYAML isn't installed, and this file
    only ever holds a flat list."""
    pins: dict[str, str | None] = {}
    in_dependencies = False
    for raw in ENVIRONMENT.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith((" ", "-")):
            in_dependencies = line.startswith("dependencies:")
            continue
        match = re.match(r"\s*-\s*([A-Za-z0-9_.-]+)\s*(?:=+\s*(\S+))?$", line)
        if in_dependencies and match:
            pins[canonicalize_name(match.group(1))] = match.group(2)
    return pins


def test_every_locked_package_is_pinned_to_one_version():
    for requirement in _requirements(LOCK):
        specifiers = list(requirement.specifier)
        assert len(specifiers) == 1 and specifiers[0].operator == "==", (
            f"{requirement} is not pinned to one version"
        )


def test_each_package_is_locked_once():
    names = [canonicalize_name(requirement.name) for requirement in _requirements(LOCK)]
    assert len(names) == len(set(names))


def test_every_conda_package_is_pinned():
    unpinned = [name for name, version in _conda_pins().items()
                if version is None and name not in {"pip"}]
    assert not unpinned, f"environment.yml leaves these to float: {unpinned}"


def test_nothing_is_installed_by_both_conda_and_pip():
    """pip replaces conda's copy of anything it is also asked for, so a package
    in both files was installed twice, and the second copy won."""
    both = sorted(set(_conda_pins()) & set(_locked()))
    assert not both, f"pinned in both environment.yml and requirements-lock.txt: {both}"


def test_every_direct_dependency_is_pinned_at_a_version_it_allows():
    """A minimum raised in requirements.txt alone, as a Dependabot pull request
    does, would otherwise go unnoticed: the image installs the pinned version."""
    pinned = {**_conda_pins(), **_locked()}
    for requirement in _requirements(DIRECT):
        name = canonicalize_name(requirement.name)
        assert pinned.get(name), f"{requirement.name} is a direct dependency but is not pinned"
        assert requirement.specifier.contains(pinned[name], prereleases=True), (
            f"requirements.txt asks for {requirement}; the image installs {pinned[name]}"
        )


@pytest.mark.parametrize("path", ["Dockerfile", "Dockerfile.legacy", ".github/workflows/ci.yml"])
def test_every_install_path_reads_the_lock(path):
    """The image, the image the deploy hosts actually build, and CI."""
    lines = (ROOT / path).read_text(encoding="utf-8").splitlines()
    commands = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    assert "requirements-lock.txt" in commands
    assert "requirements.txt" not in commands.replace("requirements-lock.txt", ""), (
        f"{path} still installs from requirements.txt"
    )
