"""Compact old tool output while retaining its useful structure."""

from __future__ import annotations

import json
import re


def semantic_preview(content: str) -> tuple[str, str]:
    stripped = content.strip()
    if stripped.startswith(("{", "[")):
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(value, dict):
                examples = [f"{key}={str(item)[:80]}" for key, item in list(value.items())[:5]
                            if isinstance(item, (str, int, float, bool))]
                return "JSON object with keys: " + ", ".join(map(str, list(value)[:20])) + "\n" + "; ".join(examples), "json"
            if isinstance(value, list):
                sample = str(value[0])[:250] if value else "(empty)"
                return f"JSON array with {len(value)} items; first item: {sample}", "json"

    lines = content.splitlines()
    if any(line.startswith(("diff --git ", "@@ ")) for line in lines):
        files = [line for line in lines if line.startswith("diff --git ")][:10]
        changes = [line for line in lines if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
        added = sum(line.startswith("+") for line in changes)
        removed = sum(line.startswith("-") for line in changes)
        hunks = [line for line in lines if line.startswith("@@ ")][:8]
        return f"Diff: {added} added, {removed} removed\n" + "\n".join([*files, *hunks, *changes[:8]])[:900], "diff"

    matches = [match for line in lines if (match := re.match(r"^(.+?):(\d+):\s*(.*)$", line))]
    if len(matches) >= 3:
        paths: dict[str, list[str]] = {}
        for match in matches:
            paths.setdefault(match[1], []).append(f"{match[1]}:{match[2]}: {match[3]}")
        kept = [f"{path} ({len(hits)} hits):\n" + "\n".join(hits[:2] + hits[-1:])
                for path, hits in list(paths.items())[:5]]
        return f"{len(matches)} search hits in {len(paths)} files:\n" + "\n".join(kept)[:600], "search"

    if re.search(r"<!doctype\s+html|<html\b", content[:1000], re.IGNORECASE):
        title = re.search(r"<title[^>]*>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
        headings = re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", content, re.IGNORECASE | re.DOTALL)

        def clean(value: str) -> str:
            return re.sub(r"<[^>]+>|\s+", " ", value).strip()

        preview = "HTML" + (f" title: {clean(title[1])}" if title else "")
        preview += "\nHeadings: " + "; ".join(clean(item) for item in headings[:8]) if headings else ""
        return preview, "html"

    signatures = [(index + 1, line.strip()) for index, line in enumerate(lines)
                  if re.match(r"\s*(?:async\s+def|def|class|function|export\s+function|fn|struct|interface)\b", line)]
    if signatures:
        imports = [line.strip() for line in lines if re.match(r"\s*(?:import|from|use|#include)\b", line)]
        preview = f"Source code, {len(lines)} lines:\n" + "\n".join(
            [*imports[:4], *(f"L{number}: {line}" for number, line in signatures[:15])])
        return preview[:700], "code"

    indexes = [index for index, line in enumerate(lines)
               if re.search(r"error|failed|traceback|exception", line, re.IGNORECASE)]
    if indexes:
        selected = sorted({near for index in indexes[-5:]
                           for near in range(max(0, index - 1), min(len(lines), index + 2))})
        return "Relevant errors and nearby lines:\n" + "\n".join(lines[index] for index in selected)[-700:], "build"

    return content[:300] + ("\n...\n" + content[-120:] if len(content) > 500 else ""), "plain"
