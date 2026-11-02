"""Version agreement: the release version is stated in four places —
the pyproject, the package ``__version__``, the API's stated version
and the lockfile's entry for this package — and all four must agree,
or the release is mislabelled in one of its surfaces."""

from __future__ import annotations

import tomllib
from pathlib import Path

import smb_commerce
from smb_commerce.api import app

ROOT = Path(__file__).resolve().parent.parent
PROJECT_NAME = "trida-blueprint-smb-commerce-operations"


def _pyproject_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def _lockfile_version() -> str:
    with (ROOT / "uv.lock").open("rb") as fh:
        lock = tomllib.load(fh)
    for package in lock["package"]:
        if package["name"] == PROJECT_NAME:
            return package["version"]
    raise AssertionError("the project package is missing from uv.lock")


def test_version_agrees_in_all_four_places():
    stated = {
        "pyproject": _pyproject_version(),
        "package __version__": smb_commerce.__version__,
        "API version": app.version,
        "uv.lock": _lockfile_version(),
    }
    assert len(set(stated.values())) == 1, stated


def test_release_version_is_1_0_0():
    assert smb_commerce.__version__ == "1.0.0"
    assert _pyproject_version() == "1.0.0"
