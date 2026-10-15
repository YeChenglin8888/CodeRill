"""Keep the durable core independent of the agent loop and user interfaces."""

import ast
from pathlib import Path


def test_core_dependency_direction():
    root = Path(__file__).resolve().parents[1] / "coderill"
    core = ("tools", "permissions", "session", "runtime", "providers", "context")
    forbidden = {"coderill.agent", "coderill.app", "coderill.cli"}
    for package in core:
        for path in (root / package).rglob("*.py"):
            module = f"coderill.{path.relative_to(root).with_suffix('').as_posix().replace('/', '.')}"
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    prefix = module.split(".")[:-node.level] if node.level else []
                    imported = ".".join(prefix + ([node.module] if node.module else []))
                elif isinstance(node, ast.Import):
                    imported = " ".join(alias.name for alias in node.names)
                else:
                    continue
                assert not any(imported == name or imported.startswith(name + ".")
                               for name in forbidden), f"{path}: forbidden import {imported}"


def test_core_import_has_no_external_side_effects():
    import subprocess
    import sys

    code = """
import socket, subprocess
def forbidden(*args, **kwargs):
    raise AssertionError('import started an external operation')
socket.socket.connect = forbidden
class BlockedPopen(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        forbidden()
subprocess.Popen = BlockedPopen
import coderill.agent.runtime
import coderill.session
import coderill.context
import coderill.permissions
import coderill.providers
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=15)
