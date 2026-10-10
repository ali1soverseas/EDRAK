"""Guard the contracts package against coming back as a second copy.

Two failure modes are checked here, both of which happened on this branch.

The first is a duplicate ``contracts`` tree at the repository root, imported as
``contracts`` while the real package is ``backend/src/edrak/contracts``. It
arrived with a merge, nothing imported it, and it had already drifted: the
copies disagreed on three files. A second copy is worse than no copy, because
which one a module picked up would decide whether pydantic accepted its
instances.

The second is importing the real package as ``backend.src.edrak.contracts``.
There are no ``__init__.py`` files at ``backend/`` or ``backend/src/``, so that
path resolves the same files under a second module identity. Measured before
the fix::

    same module object         : False
    BusinessRequest same class : False

Two classes for one schema means an instance built by an agent is not accepted
by anything this module created. It fails at a call far from the import, which
is why it is worth a test rather than a code review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "src"


def _python_files() -> list[Path]:
    skip = {".venv", "node_modules", "__pycache__", "artifacts", "graphify-out", "data"}
    return [
        path
        for path in ROOT.rglob("*.py")
        if not any(part in skip for part in path.relative_to(ROOT).parts)
    ]


def _parse(path: Path) -> ast.Module | None:
    """Parse, tolerating a byte-order mark.

    planner.py carries a UTF-8 BOM, which ast.parse rejects. Skipping it would
    hide a real import, so strip the mark and parse it like any other file.
    """
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return ast.parse(raw.decode("utf-8"), filename=str(path))
    except SyntaxError:
        return None


def test_root_contracts_package_is_absent():
    """The canonical package is edrak.contracts and nothing else."""
    assert not (ROOT / "contracts").exists(), (
        "a top-level contracts/ package reintroduces a second source of truth. "
        "Put changes in backend/src/edrak/contracts/ instead."
    )


def _absolute_imports(tree: ast.Module) -> list[tuple[int, str]]:
    """Module names imported without a leading dot.

    ``from ..contracts.CrossSignal import X`` is relative and correct: it
    reaches edrak.contracts. Only a bare ``contracts.`` refers to the deleted
    top-level package, and that is what this looks for.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            found.append((node.lineno, node.module or ""))
    return found


def test_no_module_imports_the_contracts_package_by_root_path():
    offenders = []
    for path in _python_files():
        tree = _parse(path)
        if tree is None:
            continue
        for lineno, name in _absolute_imports(tree):
            if name == "contracts" or name.startswith("contracts."):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert not offenders, (
        "import edrak.contracts, not contracts: " + ", ".join(offenders)
    )


def test_no_module_imports_edrak_through_the_backend_src_path():
    """backend.src.edrak.* loads the same files as edrak.* under a second name."""
    offenders = []
    for path in _python_files():
        tree = _parse(path)
        if tree is None:
            continue
        for lineno, name in _absolute_imports(tree):
            if name == "backend" or name.startswith("backend."):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert not offenders, (
        "import edrak.* directly. backend.src.edrak.* creates a second module "
        "identity for the same files: " + ", ".join(offenders)
    )


# The modules whose types the package is expected to re-export. Listed
# explicitly rather than discovered from dir(), because a submodule only becomes
# an attribute of the package once something imports it: DecisionAnalysis is
# attached the moment edrak.DecisionAnalysis loads, so a discovered list would
# make this test pass or fail depending on test order.
CORE_CONTRACT_MODULES = (
    "base",
    "evidence",
    "request",
    "result",
    "task",
    "verification",
    "worker",
)


def test_contracts_package_exports_every_public_type_it_defines():
    """Guards against a new core module landing without being re-exported."""
    import importlib
    import inspect

    package = importlib.import_module("edrak.contracts")
    missing = []
    for name in CORE_CONTRACT_MODULES:
        module = importlib.import_module(f"edrak.contracts.{name}")
        for attr, obj in vars(module).items():
            if attr.startswith("_") or inspect.ismodule(obj):
                continue
            if getattr(obj, "__module__", "") != module.__name__:
                continue  # imported here, defined elsewhere
            if not (inspect.isclass(obj) or inspect.isfunction(obj)):
                continue
            if not hasattr(package, attr):
                missing.append(f"{name}.{attr}")
    assert not missing, "not re-exported by edrak.contracts: " + ", ".join(missing)


@pytest.mark.parametrize("module_name", CORE_CONTRACT_MODULES + ("CrossSignal",))
def test_canonical_contract_modules_import(module_name: str):
    import importlib

    assert importlib.import_module(f"edrak.contracts.{module_name}") is not None