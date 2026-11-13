"""Tests for the golden-file manifest and fetch tool (GOLD-IO, docs/VV_PLAN.md §3)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import fetch_test_data
from fetch_test_data import Entry

FORMATS = {"nd2", "lif", "czi", "tiff", "ome-tiff"}
ALL_NAMES = sorted(fetch_test_data.load_manifest())


def _entry(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "a.nd2",
        "format": "nd2",
        "size_bytes": 3,
        "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        "url": None,
        "source": "test",
        "license": "CC0-1.0",
        "attribution": "nobody",
        "purpose": "test",
        "expected": {},
    }
    return base | overrides


def _write(tmp_path: Path, *entries: dict[str, object]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": 1, "files": list(entries)}), "utf-8")
    return path


def test_manifest_is_valid_and_complete() -> None:
    entries = fetch_test_data.load_manifest()
    assert entries
    for entry in entries.values():
        assert entry.format in FORMATS, entry.name
        assert entry.size_bytes > 0, entry.name
        assert entry.license, entry.name
        assert entry.attribution, entry.name
        assert entry.expected.get("shape"), entry.name
        assert len(entry.expected["channels"]) == entry.expected["shape"][0], entry.name


def test_public_files_are_within_download_budget() -> None:
    public = [e for e in fetch_test_data.load_manifest().values() if e.is_public]
    assert sum(e.size_bytes for e in public) < 100 * 1024 * 1024  # docs/VV_PLAN.md §3


@pytest.mark.parametrize("name", ALL_NAMES)
def test_cached_golden_file_matches_manifest(
    name: str, golden_file: Callable[[str], Path]
) -> None:
    path = golden_file(name)
    entry = fetch_test_data.load_manifest()[name]
    assert path.stat().st_size == entry.size_bytes


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"sha256": "abc"}, "sha256"),
        ({"sha256": "G" * 64}, "sha256"),
        ({"url": "http://example.org/a.nd2"}, "https"),
        ({"name": "../a.nd2"}, "bare file name"),
        ({"surprise": 1}, "unexpected"),
    ],
)
def test_invalid_manifest_entries_are_rejected(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        fetch_test_data.load_manifest(_write(tmp_path, _entry(**overrides)))


def test_manifest_missing_key_is_rejected(tmp_path: Path) -> None:
    item = _entry()
    del item["license"]
    with pytest.raises(ValueError, match="missing"):
        fetch_test_data.load_manifest(_write(tmp_path, item))


def test_duplicate_manifest_names_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        fetch_test_data.load_manifest(_write(tmp_path, _entry(), _entry()))


def test_status_reports_ok_missing_and_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch_test_data, "CACHE", tmp_path)
    entry = Entry(**_entry())  # type: ignore[arg-type]
    assert fetch_test_data.status(entry) == "missing"
    entry.path.write_bytes(b"abc")
    assert fetch_test_data.status(entry) == "ok"
    entry.path.write_bytes(b"abd")
    assert fetch_test_data.status(entry) == "corrupt"


def test_download_rejects_file_with_wrong_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "remote.bin"
    source.write_bytes(b"not abc")
    cache = tmp_path / "cache"
    monkeypatch.setattr(fetch_test_data, "CACHE", cache)
    entry = Entry(**_entry(url=source.as_uri()))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="expected"):
        fetch_test_data.download(entry)
    assert list(cache.iterdir()) == []


def test_download_stores_verified_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "remote.bin"
    source.write_bytes(b"abc")
    monkeypatch.setattr(fetch_test_data, "CACHE", tmp_path / "cache")
    entry = Entry(**_entry(url=source.as_uri()))  # type: ignore[arg-type]
    fetch_test_data.download(entry)
    assert entry.path.read_bytes() == b"abc"
    assert fetch_test_data.status(entry) == "ok"
