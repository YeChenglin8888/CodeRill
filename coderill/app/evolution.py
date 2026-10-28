"""Guided Harness² workflow for the interactive CLI."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..harness_library import ImprovementLibrary
from ..harness_tree import bind_harness, read_harness
from ..hooks import CombinedHooks
from ..memory import MemoryStore
from ..tools.agent import HarnessDelegateTool
from ..tools.skills import LoadSkillTool, SkillSearchTool

if TYPE_CHECKING:
    from .state import State


def active_marker(workspace: Path) -> Path:
    project = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:16]
    return Path.home() / ".coderill" / "active-harness" / f"{project}.txt"


def apply_active_harness(state: State, console: Console, selected: Path | None = None) -> bool:
    marker = active_marker(state.config.workspace)
    if selected is None and not marker.exists():
        return False
    try:
        root = selected or Path(marker.read_text(encoding="utf-8"))
        tree = read_harness(root)
        digest = hashlib.sha256(json.dumps(tree, sort_keys=True).encode()).hexdigest()[:16]
        local_root = state.config.workspace / ".coderill" / "harness2" / digest
        overlay, skills, hooks = bind_harness(tree, state.config.workspace, root=local_root)
        combined = CombinedHooks(state.hooks, hooks)
        agent = state.agent
        agent.harness_overlay, agent.skills, agent.hooks = overlay, skills, combined
        if skills:
            agent.registry.register(LoadSkillTool(skills))
            if state.config.routing.get("enabled", True):
                agent.registry.register(SkillSearchTool(skills, state.config.routing.get("embedding")))
        service = state.subagent_services.get(state.session.id)
        if service is not None:
            service.hooks, service.skills = combined, skills
            service.harness_overlay, service.harness_roles = overlay, tree["subagents"]
        if tree["subagents"]:
            if service is None:
                raise KeyError(f"missing subagent service for {state.session.id}")
            agent.registry.register(HarnessDelegateTool(service, tree["subagents"]))
        console.print(f"[green]Harness² 已启用：[/green] {root}")
        return True
    except (OSError, ValueError, KeyError) as exc:
        console.print(f"[red]无法加载已采纳的 Harness²：[/red] {exc}")
        return False


def _stream(command: list[str], console: Console) -> int:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", bufsize=1)
    try:
        for line in process.stdout:
            line = line.rstrip()
            if line:
                color = "green" if "verification=pass" in line or " passed" in line else (
                    "red" if "verification=fail" in line or "verification_failed" in line else "cyan")
                console.print(Text(_progress_text(line), style=color))
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        raise


def _progress_text(line: str) -> str:
    if line.startswith("{") and '"pass_rate"' in line:
        try:
            summary = json.loads(line)
            return f"评测完成：{summary['passed']}/{summary['tasks']} 通过"
        except (ValueError, KeyError, TypeError):
            pass
    line = line.replace("[editor] propose", "[编辑] 生成候选")
    line = line.replace("[editor] compose", "[编辑] 组合方案")
    line = line.replace("[editor] reflect", "[编辑] 总结经验")
    line = line.replace("[editor] reuse", "[编辑] 复用经验")
    line = line.replace("[editor] select", "[编辑] 选择方案")
    line = re.sub(r"^(\[[^]]+\]) running task$", r"\1 正在执行任务", line)
    line = re.sub(r"^(\[[^]]+\]) running$", r"\1 正在评测", line)
    line = re.sub(r"^(\[[^]]+\]) passed$", r"\1 通过", line)
    for outcome, translated in (("verification_failed", "验证失败"), ("agent_failed", "执行失败"),
                                ("infrastructure_error", "环境错误"), ("unverified", "未验证")):
        line = re.sub(rf"^(\[[^]]+\]) {outcome}$", rf"\1 {translated}", line)
    line = line.replace("status=complete", "任务完成").replace("verification=pass", "验证通过")
    line = line.replace("verification=fail", "验证未通过")
    return line


class EvolutionSession:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.snapshot: Path | None = None
        self.task = ""
        self.feedback = ""
        self.candidate: Path | None = None
        self.run_dir: Path | None = None
        self.evaluated = False
        self.verifier = ""
        self._restore_latest()

    def _restore_latest(self) -> None:
        runs = Path.home() / ".coderill" / "evolution-runs"
        for manifest in sorted(runs.glob("*/task.json"), key=lambda path: path.stat().st_mtime, reverse=True):
            try:
                task = json.loads(manifest.read_text(encoding="utf-8"))[0]
                result = json.loads((manifest.parent / "refinement" / "result.json").read_text(encoding="utf-8"))
                if Path(task["workspace"]).resolve() != self.workspace:
                    continue
                label = result.get("manual_selection", result["selection"])["choice"]
                candidate = manifest.parent / "refinement" / "harnesses" / label
                read_harness(candidate)
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                continue
            self.run_dir, self.candidate = manifest.parent, candidate
            break

    def reset(self) -> None:
        self.snapshot, self.task, self.feedback = None, "", ""
        self.candidate, self.run_dir, self.evaluated = None, None, False
        self.verifier = ""

    def track(self, console: Console) -> None:
        snapshot = Path.home() / ".coderill" / "evolution-snapshots" / uuid.uuid4().hex[:12]
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.workspace, snapshot, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", ".venv", ".coderill", "node_modules",
                                                      "__pycache__", ".env", ".env.*", ".harness2"))
        self.snapshot, self.task, self.feedback = snapshot, "", ""
        self.candidate, self.run_dir, self.evaluated = None, None, False
        self.verifier = ""
        console.print(Panel(f"已保存任务前快照：{snapshot}\n现在可以正常输入任务。",
                            title="Harness² · 任务跟踪", border_style="cyan"))

    def record_task(self, task: str, console: Console) -> None:
        if self.snapshot is None:
            return
        if self.task:
            self.reset()
            console.print("[yellow]已开始另一项任务；请重新跟踪后再优化。[/yellow]")
        else:
            self.task = task

    def refine(self, verifier: str, console: Console) -> None:
        if self.snapshot is None or not self.task:
            raise ValueError("请先保存任务前快照，再输入任务")
        argv = [part[1:-1] if len(part) > 1 and part[0] == part[-1] == '"' else part
                for part in shlex.split(verifier, posix=False)]
        if not argv:
            raise ValueError("请提供验证命令，例如 python -m pytest -q")
        self.verifier = verifier
        if argv[0].lower() in ("python", "python.exe"):
            argv[0] = "{python}"
        run_dir = Path.home() / ".coderill" / "evolution-runs" / uuid.uuid4().hex[:12]
        run_dir.mkdir(parents=True)
        manifest = run_dir / "task.json"
        manifest.write_text(json.dumps([{"id": "interactive", "workspace": str(self.workspace),
                                         "prompt": self.task, "verify": [argv]}], ensure_ascii=False),
                            encoding="utf-8")
        output = run_dir / "refinement"
        command = [sys.executable, "-u", "-m", "coderill.harness2", "--eval-manifest", str(manifest),
                   "--case-id", "interactive", "--source-snapshot", str(self.snapshot),
                   "--output", str(output), "--library", str(Path.home() / ".coderill" / "improvements"),
                   "--domain", "code", "--no-docker", "--defer-library"]
        if self.feedback:
            command += ["--feedback", self.feedback]
        console.print(Panel(f"任务：{self.task}\n反馈：{self.feedback or '无'}\n"
                            f"验证命令：{' '.join(argv)}\n执行次数：1 次基线 + 2 次候选 + 1 次组合",
                            title="Harness² · 生成 → 验证 → 组合", border_style="cyan"))
        code = _stream(command, console)
        self.run_dir, self.candidate, self.evaluated = run_dir, None, False
        result_path = output / "result.json"
        if not result_path.exists():
            raise RuntimeError(f"优化流程中断（退出码 {code}）；详情见 {output / 'error.txt'}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        table = Table(title="Harness² · 任务验证")
        table.add_column("方案")
        table.add_column("执行状态")
        table.add_column("验证结果")
        for label, run in result["runs"].items():
            table.add_row(label, "完成" if run["status"] == "complete" else run["status"],
                          "通过" if run["verification"]["passed"] else "未通过")
        console.print(table)
        choice = result["selection"]["choice"]
        if code == 0 and result["selection"].get("verified"):
            self.candidate = output / "harnesses" / choice
            console.print(f"[green]已选中 {choice}。[/green] 按 F2 可查看或改选候选，然后进行独立评测。")
        else:
            console.print(f"[red]没有通过验证的候选。[/red] 报告：{result_path}")

    def candidates(self, console: Console, *, display: bool = True) -> list[str]:
        if self.run_dir is None:
            raise ValueError("请先完成 Harness² 优化")
        result = json.loads((self.run_dir / "refinement" / "result.json").read_text(encoding="utf-8"))
        table = Table(title="Harness² · 候选方案")
        for heading in ("方案", "类型", "验证", "组件变更", "当前"):
            table.add_column(heading)
        selectable = []
        for label, run in result["runs"].items():
            passed = run["status"] == "complete" and run["verification"]["passed"] is True
            if passed and not label.startswith("base"):
                selectable.append(label)
            diff = result.get("harness_diffs", {}).get(label, {})
            changes = ", ".join((*diff.get("added", []), *diff.get("modified", []), *diff.get("removed", [])))
            kind = "基线" if label.startswith("base") else "组合" if label.startswith(("c", "agg")) else "候选"
            table.add_row(label, kind, "通过" if passed else "未通过", changes or "无",
                          "●" if self.candidate and self.candidate.name == label else "")
        if display:
            console.print(table)
        return selectable

    def select_candidate(self, label: str, console: Console) -> None:
        if label not in self.candidates(console, display=False):
            raise ValueError("只能选择执行完成且验证通过的候选方案")
        path = self.run_dir / "refinement" / "harnesses" / label
        read_harness(path)
        result_path = self.run_dir / "refinement" / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if label == result["selection"]["choice"]:
            result.pop("manual_selection", None)
        else:
            result["manual_selection"] = {"choice": label, "original": result["selection"]["choice"]}
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.candidate, self.evaluated = path, False
        console.print(f"[green]已选择 {label}。[/green] 请重新进行独立评测后再采纳。")

    def evaluate(self, manifest: Path, case_id: str, console: Console) -> None:
        if self.candidate is None or self.run_dir is None:
            raise ValueError("请先运行优化并得到通过验证的候选")
        self.evaluated = False
        cases = json.loads(manifest.read_text(encoding="utf-8"))
        selected = [case for case in cases if not case_id or case.get("id") == case_id]
        if not selected or any((manifest.parent / case["workspace"]).resolve() == self.workspace
                               for case in selected):
            raise ValueError("请选择至少一道与训练工作区不同的独立题目")
        reports = []
        for label in ("base", "candidate"):
            report = self.run_dir / f"holdout-{label}.json"
            report.unlink(missing_ok=True)
            command = [sys.executable, "-u", "-m", "examples.benchmark_suite", str(manifest),
                       "--output", str(report), "--no-docker"]
            if case_id:
                command += ["--case-id", case_id]
            if label == "candidate":
                command += ["--harness", str(self.candidate)]
            console.print(Panel(f"正在用{'基础版' if label == 'base' else '候选版'}执行 {len(selected)} 道独立题",
                                title="Harness² · 独立评测", border_style="cyan"))
            _stream(command, console)
            reports.append(json.loads(report.read_text(encoding="utf-8")))
        base = {item["id"]: item["outcome"] for item in reports[0]["results"]}
        candidate = {item["id"]: item["outcome"] for item in reports[1]["results"]}
        table = Table(title="Harness² · 独立评测对照")
        table.add_column("任务")
        table.add_column("基础版")
        table.add_column("候选版")
        for task_id, outcome in base.items():
            names = {"passed": "通过", "verification_failed": "验证失败", "agent_failed": "执行失败",
                     "infrastructure_error": "环境错误", "unverified": "未验证"}
            table.add_row(task_id, names.get(outcome, outcome), names.get(candidate.get(task_id), "缺失"))
        console.print(table)
        valid_base = {"passed", "verification_failed", "agent_failed"}
        self.evaluated = bool(base) and set(base) == set(candidate) and all(
            outcome in valid_base for outcome in base.values()) and all(
            outcome == "passed" for outcome in candidate.values())
        console.print("[green]独立评测通过。按 F2 可采纳候选。[/green]" if self.evaluated else
                      "[red]独立评测未通过，不能采纳候选。[/red]")

    def adopt(self, state: State, console: Console) -> None:
        if self.candidate is None or not self.evaluated:
            raise ValueError("采纳前需要通过任务验证和独立评测")
        state.rebuild()
        if not apply_active_harness(state, console, self.candidate):
            apply_active_harness(state, console)
            raise RuntimeError("选中的 Harness 无法启用")
        result_path = self.run_dir / "refinement" / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        selected_label = self.candidate.name
        manually_changed = selected_label != result["selection"]["choice"]
        if result.get("experience") and not result.get("library_entry") and not manually_changed:
            library = ImprovementLibrary(Path.home() / ".coderill" / "improvements", result["project"], "code")
            entry = library.add(result["experience"], selection=result["selection"], result=result_path)
            result["library_entry"] = str(entry)
            experience = result["experience"]
            MemoryStore(self.workspace).add(key=experience["cue"], kind="experience", content=experience["lesson"],
                                            evidence=experience["support"], source=f"harness:{entry}", source_type="harness")
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if manually_changed:
            result["manual_selection"] = {"choice": selected_label, "original": result["selection"]["choice"]}
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        marker = active_marker(self.workspace)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(self.candidate), encoding="utf-8")
        console.print(Panel("已用于后续任务和同一工作区的新会话。" +
                            ("手动改选的方案未写入经验库。" if manually_changed else ""),
                            title="Harness² · 已采纳", border_style="green"))

    def status(self, console: Console) -> None:
        marker = active_marker(self.workspace)
        console.print(Panel(f"任务前快照：{self.snapshot or '无'}\n任务：{self.task or '无'}\n"
                            f"反馈：{self.feedback or '无'}\n候选：{self.candidate or '无'}\n"
                            f"独立评测通过：{'是' if self.evaluated else '否'}\n已启用："
                            f"{marker.read_text(encoding='utf-8') if marker.exists() else '无'}",
                            title="Harness² · 状态", border_style="cyan"))
