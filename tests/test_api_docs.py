"""The public API is documented (UI-05)."""

from __future__ import annotations

import importlib
import inspect

import pytest

import build_api_docs


@pytest.mark.req("UI-05", "NFR-09")
@pytest.mark.parametrize("name", build_api_docs.PUBLIC_MODULES)
def test_every_public_name_has_a_docstring(name: str) -> None:
    module = importlib.import_module(name)
    assert module.__doc__, f"{name} has no module docstring"
    missing = []
    for attribute, value in vars(module).items():
        if attribute.startswith("_") or getattr(value, "__module__", None) != name:
            continue
        if inspect.isfunction(value) or inspect.isclass(value):
            if not inspect.getdoc(value):
                missing.append(attribute)
            if inspect.isclass(value):
                for method_name, method in vars(value).items():
                    if (
                        not method_name.startswith("_")
                        and inspect.isfunction(method)
                        and not inspect.getdoc(method)
                    ):
                        missing.append(f"{attribute}.{method_name}")
    assert not missing, f"{name}: undocumented public names {missing}"
