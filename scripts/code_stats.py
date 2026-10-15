"""Reproducible source counts; effective excludes blank, comment, and docstring lines."""

from __future__ import annotations

import ast
import io
import json
import tokenize
from pathlib import Path


def counts(root: Path) -> dict[str, int]:
    physical = effective = files = 0
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        physical += len(lines)
        files += 1
        doc_lines = set()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                first = node.body[0] if node.body else None
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                    doc_lines.update(range(first.lineno, first.end_lineno + 1))
        code_lines = set()
        excluded = {tokenize.ENCODING, tokenize.ENDMARKER, tokenize.INDENT, tokenize.DEDENT,
                    tokenize.NEWLINE, tokenize.NL, tokenize.COMMENT}
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type not in excluded:
                code_lines.update(range(token.start[0], token.end[0] + 1))
        effective += len(code_lines - doc_lines)
    assert 0 <= effective <= physical
    return {"files": files, "physical_lines": physical, "effective_lines": effective}


if __name__ == "__main__":
    project = Path(__file__).resolve().parents[1]
    print(json.dumps({part: counts(project / part) for part in ("coderill", "tests")}, indent=2))
