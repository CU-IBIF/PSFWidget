"""Tests for the dependency licence check (NFR-10)."""

from __future__ import annotations

from importlib import metadata
from typing import Any

import pytest

import check_licenses


class FakeDist:
    """Stand-in for an installed distribution."""

    def __init__(self, **fields: Any) -> None:
        self._fields = fields
        self.version = "1.0"

    @property
    def metadata(self) -> Any:
        fields = self._fields

        class Meta:
            def get(self, key: str, default: Any = None) -> Any:
                return fields.get(key, default)

            def get_all(self, key: str) -> Any:
                return fields.get(key)

        return Meta()


@pytest.mark.parametrize(
    ("fields", "alternatives", "acceptable"),
    [
        ({"License-Expression": "MIT"}, ["MIT"], True),
        ({"License-Expression": "BSD-3-Clause AND MIT"}, ["BSD-3-Clause AND MIT"], True),
        ({"License-Expression": "GPL-3.0-or-later"}, ["GPL-3.0-or-later"], False),
        ({"License-Expression": "AGPL-3.0-only"}, ["AGPL-3.0-only"], False),
        ({"License-Expression": "LGPL-3.0-only"}, ["LGPL-3.0-only"], True),
        (
            {"License-Expression": "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only"},
            ["LGPL-3.0-only", "GPL-2.0-only", "GPL-3.0-only"],
            True,
        ),
        (
            {"Classifier": ["License :: OSI Approved :: GNU General Public License v3 (GPLv3)"]},
            ["GNU General Public License v3 (GPLv3)"],
            False,
        ),
        (
            {
                "Classifier": [
                    "Programming Language :: Python",
                    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)",
                ]
            },
            ["GNU Lesser General Public License v3 (LGPLv3)"],
            True,
        ),
        (
            {
                "Classifier": [
                    "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
                    "License :: OSI Approved :: Mozilla Public License 1.1 (MPL 1.1)",
                ]
            },
            [
                "GNU General Public License v2 or later (GPLv2+)",
                "Mozilla Public License 1.1 (MPL 1.1)",
            ],
            True,
        ),
        ({"License": "GPL v3\nmore text"}, ["GPL v3"], False),
        ({"License": "BSD 3-Clause License"}, ["BSD 3-Clause License"], True),
        ({"License": "UNKNOWN"}, [], False),
        ({}, [], False),
    ],
)
def test_licence_policy(fields: dict[str, Any], alternatives: list[str], acceptable: bool) -> None:
    dist = FakeDist(**fields)
    assert check_licenses.licence_alternatives(dist) == alternatives  # type: ignore[arg-type]
    assert check_licenses.is_acceptable(alternatives) is acceptable


def test_find_violations(monkeypatch: pytest.MonkeyPatch) -> None:
    dists: dict[str, Any] = {
        "fine": FakeDist(**{"License-Expression": "MIT"}),
        "copyleft": FakeDist(**{"License-Expression": "GPL-3.0-only"}),
        "silent": FakeDist(),
        "checked": FakeDist(),
    }
    monkeypatch.setitem(check_licenses.VERIFIED_BY_HAND, "checked", "BSD-3-Clause")
    assert check_licenses.find_violations(dists) == [
        ("copyleft", "strong copyleft: GPL-3.0-only"),
        ("silent", "no licence in package metadata"),
    ]


def test_normalize() -> None:
    assert check_licenses.normalize("Scikit_Image.Extra") == "scikit-image-extra"


@pytest.mark.req("NFR-10")
def test_installed_dependencies_pass_the_policy(capsys: pytest.CaptureFixture[str]) -> None:
    closure = check_licenses.dependency_closure()
    for required in ("numpy", "nd2", "liffile", "czifile", "tifffile"):
        assert required in closure
    assert "psfwidget" not in closure
    assert check_licenses.find_violations(closure) == []

    assert check_licenses.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "czifile" in out
    assert out.strip().endswith("0 violation(s)")


def test_main_fails_on_violation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bad: dict[str, Any] = {"psfmodels": FakeDist(**{"License-Expression": "GPL-3.0"})}
    monkeypatch.setattr(check_licenses, "dependency_closure", lambda: bad)
    assert check_licenses.main([]) == 1
    captured = capsys.readouterr()
    assert "LICENCE VIOLATION psfmodels: strong copyleft: GPL-3.0" in captured.err
    assert "1 violation(s)" in captured.out


def test_dependency_closure_skips_uninstalled_optional_packages() -> None:
    with pytest.raises(metadata.PackageNotFoundError):
        check_licenses.dependency_closure("no-such-distribution-xyz")
