"""CodeRill's eight editable Harness² components and their runtime binding."""

from __future__ import annotations

import ast
import json
import re
from copy import deepcopy
from pathlib import Path

from .hooks import Hooks
from .skills import SkillCatalog, default_roots

COMPONENTS = ("system_prompt", "guardrails", "memory", "tool_guidance", "skills",
              "scripts", "subagents", "middleware")
_NAMED = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_TEXT = COMPONENTS[:4]
_MAP = COMPONENTS[4:7]
_TEXT_FILES = {"system_prompt": "systemprompt.md", "guardrails": "guardrails.md",
               "memory": "LongTermMEMORY.md", "tool_guidance": "tool_descriptions/tool_guidance.md"}


def base_harness() -> dict:
    return {**{name: "" for name in _TEXT}, **{name: {} for name in _MAP},
            "middleware": {"blocked_tools": []}}


def edit_harness(base: dict, patch: dict) -> dict:
    """Apply an omitted-is-inherited edit, validating every component before use."""
    if not isinstance(patch, dict) or not patch or set(patch) - set(COMPONENTS):
        raise ValueError("harness edit must change known components")
    tree = deepcopy(base)
    for name, value in patch.items():
        if name in _TEXT:
            if value is not None and (not isinstance(value, str) or len(value) > 3000):
                raise ValueError(f"{name} must be text under 3000 characters")
            tree[name] = value or ""
        elif name in _MAP:
            if not isinstance(value, dict):
                raise ValueError(f"{name} must be a name-to-text map")
            for key, body in value.items():
                if not isinstance(key, str) or not _NAMED.fullmatch(key):
                    raise ValueError(f"invalid {name} name: {key!r}")
                if body is None:
                    tree[name].pop(key, None)
                    continue
                if not isinstance(body, str) or not body.strip() or len(body) > 5000:
                    raise ValueError(f"{name}.{key} must contain 1-5000 characters")
                if name == "scripts":
                    ast.parse(body, filename=f"{key}.py")
                tree[name][key] = body
        else:
            if not isinstance(value, dict) or set(value) - {"blocked_tools", "pre_rules", "post_rules"}:
                raise ValueError("middleware accepts blocked_tools, pre_rules and post_rules")
            updated = {**tree[name], **value}
            blocked = updated.get("blocked_tools", [])
            if (not isinstance(blocked, list) or len(blocked) > 32 or
                any(not isinstance(item, str) or not _NAMED.fullmatch(item) for item in blocked)):
                raise ValueError("middleware.blocked_tools must contain up to 32 tool names")
            updated["blocked_tools"] = sorted(set(blocked))
            for event, field in (("pre_rules", "argument_contains"), ("post_rules", "result_contains")):
                rules = updated.get(event, [])
                if (not isinstance(rules, list) or len(rules) > 8 or any(
                    not isinstance(rule, dict) or set(rule) != {"tool", field, "message"} or
                    rule["tool"] != "*" and (not isinstance(rule["tool"], str) or
                    not _NAMED.fullmatch(rule["tool"])) or
                    not isinstance(rule[field], str) or not 1 <= len(rule[field]) <= 200 or
                    not isinstance(rule["message"], str) or not 1 <= len(rule["message"]) <= 500
                    for rule in rules
                )):
                    raise ValueError(f"middleware.{event} has invalid tool, match text or message")
            tree[name] = updated
    if sum(len(str(part)) for part in tree.values()) > 24000:
        raise ValueError("harness exceeds 24000 characters")
    return tree


class HarnessHooks(Hooks):
    """Declarative tool-loop plugin that cannot execute host shell commands."""

    def __init__(self, middleware: dict) -> None:
        super().__init__([], [])
        self.blocked_tools = set(middleware.get("blocked_tools", []))
        self.pre_rules = middleware.get("pre_rules", [])
        self.post_rules = middleware.get("post_rules", [])

    def __bool__(self) -> bool:
        return bool(self.blocked_tools or self.pre_rules or self.post_rules)

    def run_pre(self, tool_name: str, tool_input: dict) -> str | None:
        if tool_name in self.blocked_tools:
            return "blocked by task harness middleware"
        arguments = json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
        for rule in self.pre_rules:
            if rule["tool"] in ("*", tool_name) and rule["argument_contains"] in arguments:
                return rule["message"]
        return None

    def run_post(self, tool_name: str, tool_input: dict, result: str) -> str | None:
        hints = [rule["message"] for rule in self.post_rules
                 if rule["tool"] in ("*", tool_name) and rule["result_contains"] in result]
        return result + "\n" + "\n".join(hints) if hints else None


def harness_files(tree: dict) -> dict[str, str]:
    """Map the validated component tree to its auditable file surface."""
    files = {_TEXT_FILES[name]: tree[name] for name in _TEXT}
    files.update({f"skills/{name}/SKILL.md": body for name, body in tree["skills"].items()})
    files.update({f"scripts/{name}.py": body for name, body in tree["scripts"].items()})
    files.update({f"sub_agents/{name}/AGENT.md": body for name, body in tree["subagents"].items()})
    files["plugin/tool_loop.json"] = json.dumps(tree["middleware"], ensure_ascii=False, indent=2) + "\n"
    return files


def write_harness(tree: dict, dest: Path) -> Path:
    """Write a full harness into a new directory; never replace an earlier candidate."""
    edit_harness(base_harness(), tree)
    dest.mkdir(parents=True, exist_ok=False)
    for relative, body in harness_files(tree).items():
        path = dest / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return dest


def read_harness(root: Path) -> dict:
    """Read only known component paths from a stored harness tree."""
    if not root.is_dir() or root.is_symlink():
        raise ValueError("harness directory is missing or a symlink")
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("harness tree cannot contain symlinks")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    tree = base_harness()
    for name, relative in _TEXT_FILES.items():
        tree[name] = files.pop(relative, "")
    for component, prefix, suffix in (("skills", "skills/", "/SKILL.md"),
                                      ("scripts", "scripts/", ".py"),
                                      ("subagents", "sub_agents/", "/AGENT.md")):
        for relative in list(files):
            if relative.startswith(prefix) and relative.endswith(suffix):
                name = relative[len(prefix):-len(suffix)]
                tree[component][name] = files.pop(relative)
    middleware = files.pop("plugin/tool_loop.json", "{}")
    if files:
        raise ValueError(f"off-surface harness files: {sorted(files)}")
    return edit_harness(base_harness(), {**tree, "middleware": json.loads(middleware)})


def diff_harness(before: dict, after: dict) -> dict[str, list[str]]:
    old, new = harness_files(before), harness_files(after)
    return {"added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys()),
            "modified": sorted(path for path in old.keys() & new.keys() if old[path] != new[path])}


def bind_harness(tree: dict, workspace: Path, *, root: Path | None = None) -> tuple[str, SkillCatalog, HarnessHooks]:
    """Materialize code and skill files only inside an isolated probe workspace."""
    if root is None:
        root = workspace / ".harness2"
        write_harness(tree, root)
    elif root.exists():
        if read_harness(root) != tree:
            raise ValueError("materialized harness differs from the selected candidate")
    else:
        write_harness(tree, root)
    catalog = SkillCatalog([root / "skills", *default_roots(workspace)])
    sections = [(name.replace("_", " ").title(), tree[name]) for name in _TEXT if tree[name]]
    if tree["scripts"]:
        scripts = (root / "scripts").relative_to(workspace).as_posix()
        sections.append(("Scripts", f"Available in {scripts}/: " + ", ".join(
            f"{name}.py" for name in sorted(tree["scripts"]))))
    if tree["subagents"]:
        sections.append(("Subagent Roles", "\n".join(f"{name}: {body}" for name, body in
                                                  sorted(tree["subagents"].items())) +
                         "\nUse harness_agent(role, task) for a matching read-only investigation."))
    overlay = "\n\n".join(f"# {heading}\n{body}" for heading, body in sections)
    return overlay, catalog, HarnessHooks(tree["middleware"])
