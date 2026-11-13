"""Check that no installed dependency of psfwidget is under a strong copyleft licence.

psfwidget is MIT-licensed (NFR-10). This tool walks the installed dependency
tree of the ``psfwidget`` distribution, reads each licence from the package
metadata, and fails on GPL or AGPL licences and on packages whose licence
cannot be determined. A package offered under several licences passes if any
alternative is acceptable (for example ``LGPL-3.0-only OR GPL-3.0-only``).

Usage::

    python tools/check_licenses.py          # exit 1 on a violation
    python tools/check_licenses.py --list   # also print every package and licence
"""

from __future__ import annotations

import argparse
import re
import sys
from importlib import metadata

from packaging.requirements import Requirement

ROOT_PACKAGE = "psfwidget"

# Strong copyleft: GPL and AGPL, but not LGPL.
_COPYLEFT = re.compile(r"(?<![A-Za-z])A?GPL|(?<!Lesser )General Public Licen[sc]e", re.IGNORECASE)

# Packages whose metadata carries no usable licence field, checked by hand.
# Map of normalized name -> licence found in the project's repository.
VERIFIED_BY_HAND: dict[str, str] = {}


def normalize(name: str) -> str:
    """Return the PEP 503 normalized form of a distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def licence_alternatives(dist: metadata.Distribution) -> list[str]:
    """Return the licences a distribution is offered under, as alternatives."""
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return [part.strip(" ()") for part in re.split(r"\s+OR\s+", expression)]
    classifiers = [
        value.split("::")[-1].strip()
        for value in meta.get_all("Classifier") or []
        if value.startswith("License ::")
    ]
    if classifiers:
        return classifiers
    text = (meta.get("License") or "").strip()
    if text and text.upper() != "UNKNOWN":
        return [text.splitlines()[0][:80]]
    return []


def is_acceptable(alternatives: list[str]) -> bool:
    """Whether at least one licence alternative is not strong copyleft."""
    return any(not _COPYLEFT.search(licence) for licence in alternatives)


def dependency_closure(root: str = ROOT_PACKAGE) -> dict[str, metadata.Distribution]:
    """Return the installed distributions reachable from ``root``, with all its extras."""
    root_dist = metadata.distribution(root)
    extras = set(root_dist.metadata.get_all("Provides-Extra") or [])
    found: dict[str, metadata.Distribution] = {}
    queue: list[tuple[metadata.Distribution, set[str]]] = [(root_dist, extras)]
    while queue:
        dist, active_extras = queue.pop()
        for line in dist.requires or []:
            requirement = Requirement(line)
            environments = [{"extra": extra} for extra in active_extras] or [{"extra": ""}]
            if requirement.marker is not None and not any(
                requirement.marker.evaluate(env) for env in environments
            ):
                continue
            name = normalize(requirement.name)
            if name == normalize(root):
                continue  # self-reference such as psfwidget[test]
            try:
                child = metadata.distribution(requirement.name)
            except metadata.PackageNotFoundError:
                continue  # optional dependency that is not installed
            if name not in found:
                found[name] = child
                queue.append((child, set(requirement.extras)))
    return found


def find_violations(
    dists: dict[str, metadata.Distribution],
) -> list[tuple[str, str]]:
    """Return ``(name, reason)`` for each distribution that fails the policy."""
    violations: list[tuple[str, str]] = []
    for name, dist in sorted(dists.items()):
        alternatives = licence_alternatives(dist)
        if not alternatives and name in VERIFIED_BY_HAND:
            alternatives = [VERIFIED_BY_HAND[name]]
        if not alternatives:
            violations.append((name, "no licence in package metadata"))
        elif not is_acceptable(alternatives):
            violations.append((name, "strong copyleft: " + "; ".join(alternatives)))
    return violations


def main(argv: list[str] | None = None) -> int:
    """Run the licence check; return a process exit code."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--list", action="store_true", help="print every package and licence")
    args = parser.parse_args(argv)

    dists = dependency_closure()
    if args.list:
        for name, dist in sorted(dists.items()):
            print(f"{name:28} {dist.version:14} {'; '.join(licence_alternatives(dist)) or '?'}")
    violations = find_violations(dists)
    for name, reason in violations:
        print(f"LICENCE VIOLATION {name}: {reason}", file=sys.stderr)
    print(f"checked {len(dists)} packages, {len(violations)} violation(s)")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
