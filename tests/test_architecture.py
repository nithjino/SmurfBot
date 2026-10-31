"""Structural rules that keep the Discord dependency in one place."""

import ast
import subprocess
import sys
from pathlib import Path

import smurfbot

PACKAGE_DIR = Path(smurfbot.__file__).parent
TRANSPORT_MODULES = {"gateway.py"}
ENTRYPOINT = "__main__.py"


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def test_only_the_gateway_imports_discord_at_runtime() -> None:
    offenders = [
        path.name
        for path in PACKAGE_DIR.rglob("*.py")
        if path.name not in {*TRANSPORT_MODULES, ENTRYPOINT} and "discord" in imported_modules(path)
    ]

    assert offenders == []


def test_the_application_loads_without_discord() -> None:
    code = "import sys, smurfbot.app; sys.exit('discord' in sys.modules)"

    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0  # noqa: S603
