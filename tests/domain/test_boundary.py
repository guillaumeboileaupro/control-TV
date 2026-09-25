"""Architecture boundary: the shared domain stays free of Cast, UI and MCP concerns."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import control_tv.domain as domain

DOMAIN_DIR = Path(domain.__file__).parent
CONTROL_MODULES = ("ports.py", "service.py")


def imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def is_allowed(module: str) -> bool:
    top_level = module.split(".")[0]
    return top_level in sys.stdlib_module_names or module.startswith("control_tv.domain")


def test_domain_imports_only_the_standard_library_and_itself() -> None:
    sources = sorted(DOMAIN_DIR.glob("*.py"))
    assert sources, "domain sources not found"

    offenders = {
        f"{path.name}: {module}"
        for path in sources
        for module in imported_modules(path.read_text())
        if not is_allowed(module)
    }

    assert offenders == set()


def test_boundary_check_detects_a_forbidden_import() -> None:
    assert not is_allowed("pychromecast")
    assert not is_allowed("mcp.server")
    assert not is_allowed("control_tv.adapters.cast")
    assert is_allowed("dataclasses")
    assert is_allowed("control_tv.domain.errors")


def test_public_exports_all_resolve() -> None:
    assert domain.__all__ == sorted(domain.__all__)
    for name in domain.__all__:
        assert hasattr(domain, name), name


def test_control_layer_stays_independent_of_cast_libraries_ui_and_mcp() -> None:
    offenders = {
        f"{name}: {module}"
        for name in CONTROL_MODULES
        for module in imported_modules((DOMAIN_DIR.parent / name).read_text())
        if not (module.split(".")[0] in sys.stdlib_module_names or module.startswith("control_tv"))
    }

    assert offenders == set()
