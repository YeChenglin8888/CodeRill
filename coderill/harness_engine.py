"""Task and cross-task recursion for the CodeRill Harness² runner."""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from copy import deepcopy

from .harness_tree import COMPONENTS, base_harness, diff_harness, edit_harness


def _quote(run: dict, quote: str) -> bool:
    return isinstance(quote, str) and len(quote) >= 12 and quote in (
        run.get("trace", "") + run.get("changes", "") + run.get("text", ""))


def _proposal(base: dict, answer: dict) -> dict:
    if not isinstance(answer, dict):
        raise TypeError("editor must return a JSON object")
    if answer.get("patch") == {}:
        return deepcopy(base)
    return edit_harness(base, answer.get("patch"))


async def _stage(editor, name: str, evidence: dict, base: dict) -> dict:
    error = ""
    for _ in range(2):
        try:
            return _proposal(base, await editor(name, {**evidence, "validation_error": error}))
        except (ValueError, TypeError, SyntaxError) as exc:
            error = str(exc)
    return deepcopy(base)


async def run_harness(task: str, probe, editor, library: list[dict], *,
                      mode: str = "seq", k: int = 1, selector=None, reuse_selector=None) -> dict:
    """Run 4k matched rollouts: shared bases, two probes and one final per step."""
    if mode not in ("seq", "agg") or not 1 <= k <= 8:
        raise ValueError("mode must be seq or agg; k must be 1-8")
    started = time.monotonic()
    tree = base_harness()
    reuse_decision = None
    if library:
        # Each new task starts from the common base; only explicitly selected components transfer.
        selected_index = None
        if reuse_selector is not None:
            reuse_decision = await reuse_selector(task, library)
            selected_index = reuse_decision.get("index")
            if selected_index is not None and (type(selected_index) is not int or
                                               selected_index not in range(len(library))):
                raise ValueError("reuse selector chose an unknown experience")
        reuse = (await editor("reuse", {"task": task, "library": library,
                                        "selected_index": selected_index, "components": COMPONENTS})
                 if reuse_selector is None or selected_index is not None else {})
        if isinstance(reuse, dict):
            picks = (reuse.get("entries") if reuse_selector is None else None)
            if picks is None:
                picks = [{"index": selected_index if reuse_selector is not None else reuse.get("index"),
                          "components": reuse.get("components", [])}]
            if not isinstance(picks, list) or len(picks) > 8:
                raise ValueError("reuse must select at most eight entries")
            patch = {}
            for pick in picks:
                if not isinstance(pick, dict):
                    raise TypeError("reuse entries must be objects")
                index, names = pick.get("index"), pick.get("components")
                if index is None:
                    continue
                if (type(index) is not int or index not in range(len(library)) or
                    not isinstance(names, list) or not names or
                    any(not isinstance(name, str) or name not in COMPONENTS for name in names) or
                    not isinstance(library[index].get("harness"), dict)):
                    raise ValueError("reuse selected an invalid entry or component")
                for name in names:
                    if name in patch:
                        raise ValueError(f"reuse selected {name} from multiple entries")
                    patch[name] = library[index]["harness"][name]
            if patch:
                tree = edit_harness(tree, patch)
    bases = await asyncio.gather(*(probe(f"base{i}", tree) for i in range(1, k + 1)))
    baseline = {f"base{i}": run for i, run in enumerate(bases, 1)}
    runs = dict(baseline)
    trees = {label: tree for label in baseline}
    diffs = {}
    stages = []

    async def proposals(index: int, current: dict, previous: dict) -> dict:
        async def one(dimension: str) -> tuple[str, dict]:
            label = f"{dimension}{index}"
            candidate = await _stage(editor, "propose", {"task": task, "dimension": dimension,
                "index": index, "baseline": baseline, "previous": previous,
                "current_harness": current, "library": library}, current)
            return label, candidate

        pair = await asyncio.gather(one("inv"), one("dom"))
        candidates = dict(pair)
        results = await asyncio.gather(*(probe(label, candidate)
                                         for label, candidate in pair))
        runs.update(zip(candidates, results))
        trees.update(candidates)
        diffs.update({label: diff_harness(current, candidate) for label, candidate in candidates.items()})
        stages.extend(candidates)
        return candidates

    async def compose(index: int, current: dict, candidates: dict, previous: dict) -> dict:
        label = f"c{index}" if mode == "seq" else f"agg{index}"
        pool = {name: runs[name] for name in candidates}
        base_label = "base1" if mode == "agg" or index == 1 else f"c{index - 1}"
        pool[base_label] = runs[base_label]
        error = ""
        for _ in range(2):
            try:
                answer = await editor("compose", {"task": task, "index": index,
                    "baseline": baseline, "pool": pool, "candidates": candidates,
                    "current_harness": current, "previous": previous, "library": library,
                    "validation_error": error})
                citations = answer["citations"]
                if (not isinstance(citations, dict) or base_label not in citations or
                    not any(name in citations for name in candidates) or
                    any(name not in pool or not _quote(pool[name], quote)
                        for name, quote in citations.items())):
                    raise ValueError("composition needs exact excerpts from the current and a candidate run")
                composed = _proposal(current, answer)
                fallback = False
                break
            except (KeyError, TypeError, ValueError, SyntaxError) as exc:
                error = str(exc)
        else:
            composed, citations, fallback = deepcopy(current), {}, True
        trees[label] = composed
        diffs[label] = diff_harness(current, composed)
        runs[label] = await probe(label, composed)
        stages.append(label)
        return {"label": label, "harness": composed, "citations": citations, "fallback": fallback}

    finals = []
    if mode == "agg":
        # Width: 2k independent proposals use the same base evidence, then k compositions.
        candidate_groups = await asyncio.gather(*(proposals(i, tree, {}) for i in range(1, k + 1)))
        all_candidates = {label: harness for group in candidate_groups for label, harness in group.items()}
        finals = await asyncio.gather(*(compose(i, tree, all_candidates, {})
                                        for i in range(1, k + 1)))
    else:
        # Depth: every composition becomes the next step's current harness.
        current, previous = tree, {}
        for i in range(1, k + 1):
            candidates = await proposals(i, current, previous)
            final = await compose(i, current, candidates, previous)
            finals.append(final)
            current = final["harness"]
            previous = {label: runs[label] for label in (*candidates, final["label"])}

    verified = any("verification" in run for run in runs.values())
    if verified and not all("verification" in run for run in runs.values()):
        raise ValueError("every probe must report verification when verification is enabled")
    delivered = [item for item in finals if runs[item["label"]].get("status") == "complete"]
    if verified:
        passing = [item for item in delivered if runs[item["label"]]["verification"]["passed"] is True]
        if not passing:
            passing = [{"label": label, "harness": trees[label], "citations": {}}
                       for label in stages if label.startswith(("inv", "dom"))
                       and runs[label].get("status") == "complete"
                       and runs[label]["verification"]["passed"] is True]
        if not passing:
            passing = [{"label": label, "harness": trees[label], "citations": {}}
                       for label in baseline if runs[label].get("status") == "complete"
                       and runs[label]["verification"]["passed"] is True]
        delivered = passing or [finals[0]]
    elif not delivered:
        raise RuntimeError("no final harness completed the task")
    if len(delivered) == 1:
        selected = delivered[0]
        selection = {"choice": selected["label"], "method": "single"}
    else:
        # Candidate labels and harness internals stay out of the selector's view.
        shuffled = list(delivered)
        seed = int(hashlib.sha256(f"{task}|{mode}|{k}".encode()).hexdigest()[:16], 16)
        random.Random(seed).shuffle(shuffled)
        anon = {chr(65 + index): item for index, item in enumerate(shuffled)}
        evidence = {}
        for key, item in anon.items():
            run = runs[item["label"]]
            workspace = run.get("workspace", "")
            evidence[key] = {part: run.get(part, "").replace(workspace, "<workspace>")
                             if workspace else run.get(part, "") for part in ("text", "changes")}
        if selector is None:
            answer = await editor("select", {"task": task, "candidates": evidence})
        else:
            answer = await selector(task, evidence)
        choice = answer.get("choice") if isinstance(answer, dict) else None
        if choice not in anon:
            raise ValueError("selector must choose a completed final deliverable")
        selected = anon[choice]
        selection = {"choice": selected["label"], "anonymous_choice": choice,
                     "method": "jev" if selector else "editor", "decision": answer}

    if verified:
        selection["verified"] = runs[selected["label"]]["verification"]["passed"] is True
    reflection_error = ""
    reflection = {}
    if not verified or (selection["verified"] and selected["label"] not in baseline):
        try:
            reflection = await editor("reflect", {"task": task, "selection": selection,
                "harness": selected["harness"], "citations": selected["citations"],
                "final": runs[selected["label"]], "stages": stages})
        except Exception as exc:  # noqa: BLE001 - an optional lesson must not discard the completed deliverable
            reflection_error = f"{type(exc).__name__}: {exc}"
    experience = None
    if isinstance(reflection, dict) and _quote(runs[selected["label"]], reflection.get("support")):
        lesson, cue = reflection.get("lesson"), reflection.get("cue")
        if (isinstance(lesson, str) and 12 <= len(lesson) <= 500 and
            isinstance(cue, str) and 5 <= len(cue) <= 200):
            experience = {"harness": selected["harness"], "lesson": lesson,
                          "cue": cue, "support": reflection["support"],
                          "edits": reflection.get("edits", "") if isinstance(reflection.get("edits", ""), str) else ""}
    usage = {key: sum(run.get("usage", {}).get(key, 0) for run in runs.values())
             for key in ("requests", "input_tokens", "output_tokens")}
    return {"mode": mode, "k": k, "runs": runs, "harnesses": trees, "finals": finals,
            "harness_diffs": diffs, "rollout_usage": usage,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "selection": selection, "reuse_decision": reuse_decision,
            "experience": experience, "reflection_error": reflection_error,
            "workspace": runs[selected["label"]].get("workspace", "")}
