"""Smoke tests for the package skeleton and CLI."""

from __future__ import annotations

import importlib
import re
import subprocess
import sys

import pytest

import psfwidget
from psfwidget.automation import cli

SUBPACKAGES = [
    "io",
    "profiles",
    "detect",
    "metrics",
    "theory",
    "average",
    "tracking",
    "report",
    "automation",
    "napari",
]


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+((a|b|rc)\d+)?(\.dev\d+)?", psfwidget.__version__)


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports_without_optional_deps(name: str) -> None:
    module = importlib.import_module(f"psfwidget.{name}")
    assert module.__doc__


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"psfwidget {psfwidget.__version__}"


def test_cli_without_arguments_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 0
    assert "usage: psfwidget" in capsys.readouterr().out


@pytest.mark.req("AUT-01")
def test_module_entry_point_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "psfwidget", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert psfwidget.__version__ in result.stdout
