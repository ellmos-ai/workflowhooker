"""workflowhooker must keep working against the installed hook_master.

A hook that dies on import fails open (non-blocking status code), so a version
drift between the pinned hook_master and the one actually installed silently
switches the Stop/PreCompact gates off.  Ticket T-20261101-872496680.
"""

import ast
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "workflowhooker"


def test_cli_imports_without_shadow_only_helper():
    """`powershell_command` is needed for the Codex/Windows shadow snippet only.

    Without it, `import workflowhooker.cli` (every `hook-run`) must still work.
    """
    code = (
        "import hook_master.providers.codex as c;"
        "c.__dict__.pop('powershell_command', None);"
        "import workflowhooker.cli"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])}
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT
    )
    assert result.returncode == 0, result.stderr


def _hook_master_imports():
    for path in sorted(PACKAGE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module
                and node.module.split(".")[0] == "hook_master"
            ):
                yield path.relative_to(ROOT), node


def test_every_imported_hook_master_symbol_exists():
    """Includes function-level imports: drift must stay loud even where it is no longer fatal."""
    installed = importlib.import_module("hook_master")
    pin = re.search(
        r"hook-master @ git\+\S+@([0-9a-f]{40})",
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
    )
    missing = []
    for path, node in _hook_master_imports():
        module = importlib.import_module(node.module)
        missing += [
            f"{path}:{node.lineno} {node.module}.{alias.name}"
            for alias in node.names
            if not hasattr(module, alias.name)
        ]
    assert not missing, (
        f"installed hook_master ({installed.__file__}) lacks symbols; "
        f"pyproject pins {pin.group(1) if pin else '?'}: " + "; ".join(missing)
    )
