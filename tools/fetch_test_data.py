"""Download and verify the golden test files listed in ``tests/data/manifest.json``.

Golden files are not stored in git. Each manifest entry records a SHA-256
hash and, for publicly hosted files, a download URL. Files are cached in
``tests/data/cache/`` (git-ignored). Entries without a URL are local-only:
copy the file into the cache by hand and this tool verifies it.

Usage::

    python tools/fetch_test_data.py            # download missing files, verify all
    python tools/fetch_test_data.py --status   # report only; download nothing
    python tools/fetch_test_data.py --require-public   # exit 1 if a public file is missing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "data" / "manifest.json"
CACHE = ROOT / "tests" / "data" / "cache"

REQUIRED_KEYS = frozenset(
    {
        "name",
        "format",
        "size_bytes",
        "sha256",
        "url",
        "source",
        "license",
        "attribution",
        "purpose",
        "expected",
    }
)


@dataclass(frozen=True)
class Entry:
    """One golden file in the manifest."""

    name: str
    format: str
    size_bytes: int
    sha256: str
    url: str | None
    source: str
    license: str
    attribution: str
    purpose: str
    expected: dict[str, Any]

    @property
    def path(self) -> Path:
        """Location of the file in the local cache."""
        return CACHE / self.name

    @property
    def is_public(self) -> bool:
        """Whether the file can be downloaded."""
        return self.url is not None


def load_manifest(path: Path = MANIFEST) -> dict[str, Entry]:
    """Read and validate the manifest; return entries keyed by file name."""
    raw = json.loads(path.read_text("utf-8"))
    entries: dict[str, Entry] = {}
    for item in raw["files"]:
        missing = REQUIRED_KEYS - item.keys()
        extra = item.keys() - REQUIRED_KEYS
        if missing or extra:
            raise ValueError(
                f"manifest entry {item.get('name')!r}: missing {sorted(missing)}, "
                f"unexpected {sorted(extra)}"
            )
        entry = Entry(**item)
        if entry.name in entries:
            raise ValueError(f"duplicate manifest entry: {entry.name}")
        if Path(entry.name).name != entry.name:
            raise ValueError(f"manifest name must be a bare file name: {entry.name}")
        if len(entry.sha256) != 64 or set(entry.sha256) - set("0123456789abcdef"):
            raise ValueError(f"{entry.name}: sha256 must be 64 lowercase hex characters")
        if entry.url is not None and not entry.url.startswith("https://"):
            raise ValueError(f"{entry.name}: url must use https")
        entries[entry.name] = entry
    return entries


def sha256_of(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def status(entry: Entry) -> str:
    """Return ``"ok"``, ``"missing"``, or ``"corrupt"`` for a cached file."""
    if not entry.path.is_file():
        return "missing"
    return "ok" if sha256_of(entry.path) == entry.sha256 else "corrupt"


def download(entry: Entry) -> None:
    """Download one public file into the cache, verifying its hash first."""
    if entry.url is None:
        raise ValueError(f"{entry.name} has no download URL")
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=CACHE, suffix=".part", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        try:
            with urllib.request.urlopen(entry.url, timeout=120) as response:
                shutil.copyfileobj(response, tmp)
            tmp.flush()
            actual = sha256_of(tmp_path)
            if actual != entry.sha256:
                raise ValueError(
                    f"{entry.name}: downloaded file has sha256 {actual}, expected {entry.sha256}"
                )
        except BaseException:
            tmp.close()
            tmp_path.unlink(missing_ok=True)
            raise
    tmp_path.replace(entry.path)


def main(argv: list[str] | None = None) -> int:
    """Fetch and verify golden files; return a process exit code."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--status", action="store_true", help="report only; download nothing")
    parser.add_argument(
        "--require-public",
        action="store_true",
        help="exit 1 if any downloadable file is missing afterwards",
    )
    args = parser.parse_args(argv)

    failed = False
    for entry in load_manifest().values():
        state = status(entry)
        if state != "ok" and entry.is_public and not args.status:
            try:
                download(entry)
                state = "ok (downloaded)"
            except (OSError, ValueError) as exc:
                state = f"download failed: {exc}"
        kind = "public" if entry.is_public else "local-only"
        print(f"{entry.name:28} {kind:10} {state}")
        if state == "corrupt" or (
            args.require_public and entry.is_public and not state.startswith("ok")
        ):
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
