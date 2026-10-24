"""The router: a decision GRAPH with named nodes, not an if/else.

Online node set (frozen by INTERFACE.md):

    embed_task -> match_skills -> (skill_hit -> load_parts -> run_skill)
                               |  (no_hit -> fallback_frontier)
                               -> log_trace

Every run records the exact node sequence it took in `decision_path`, which
the dashboard renders over the graph. Discovery adds its own offline nodes
(cluster_history -> propose_skill -> overlap_check -> judge_gate ->
promote|reject|merge); those live in the discovery slice, not here.
"""
from __future__ import annotations

import os

from ..router.embeddings import Embedder, cosine
from ..router.skill_store import list_skills, record_hit
from ..runner.executor import run_frontier, run_skill
from ..traces.logger import append_trace, make_trace

# Match threshold on cosine similarity. Defaults are tuned per embedding
# backend (hash cosines run lower than model embeddings); MATCH_THRESHOLD
# overrides either way. Calibrated against the corpus: for a commit-message
# skill trigger, all 5 commit_message prompts sit above and every other
# workflow (including the other diff-based ones) sits below.
DEFAULT_THRESHOLD_BY_BACKEND = {
    "hash": 0.44,
    "gemini": 0.76,
    "openai": 0.78,
}


class DecisionGraph:
    def __init__(self, client, embedder: Embedder | None = None,
                 threshold: float | None = None, skills_dir=None,
                 traces_file=None):
        self.client = client
        self.embedder = embedder or Embedder()
        default_thr = DEFAULT_THRESHOLD_BY_BACKEND.get(self.embedder.backend, 0.44)
        self.threshold = (
            threshold
            if threshold is not None
            else float(os.environ.get("MATCH_THRESHOLD", default_thr))
        )
        self.skills_dir = skills_dir
        self.traces_file = traces_file
        # node name -> callable(ctx) -> next node name (or None to stop)
        self.nodes = {
            "embed_task": self._embed_task,
            "match_skills": self._match_skills,
            "skill_hit": self._skill_hit,
            "load_parts": self._load_parts,
            "run_skill": self._run_skill,
            "no_hit": self._no_hit,
            "fallback_frontier": self._fallback_frontier,
            "log_trace": self._log_trace,
        }

    # ---- public API ------------------------------------------------

    def route(self, task) -> dict:
        """Run one task through the graph. Returns the logged trace dict."""
        ctx = {"task": task, "decision_path": []}
        node = "embed_task"
        while node is not None:
            ctx["decision_path"].append(node)
            node = self.nodes[node](ctx)
        return ctx["trace"]

    # ---- nodes ------------------------------------------------------

    def _embed_task(self, ctx) -> str:
        # Embed the PROMPT, not the payload: the workflow's shape lives in
        # what is being asked ("write a commit message"), while the context
        # is instance payload. Calibrated against the corpus - prompt-only
        # separates the 7 recurring workflows from each other and from the
        # one-off controls; adding context only dilutes the signal.
        ctx["task_vec"] = self.embedder.embed(ctx["task"].prompt)
        return "match_skills"

    def _match_skills(self, ctx) -> str:
        skills = list_skills(self.skills_dir)
        best_skill, best_sim = None, -1.0
        for skill in skills:
            target = f"{skill.get('trigger_description', '')}\n{skill.get('responsibility', '')}"
            sim = cosine(ctx["task_vec"], self.embedder.embed(target))
            if sim > best_sim:
                best_skill, best_sim = skill, sim
        ctx["match"] = best_skill if (best_skill and best_sim >= self.threshold) else None
        ctx["match_sim"] = round(best_sim, 4)
        return "skill_hit" if ctx["match"] else "no_hit"

    def _skill_hit(self, ctx) -> str:
        ctx["skill"] = ctx["match"]
        return "load_parts"

    def _load_parts(self, ctx) -> str:
        """Selective loading: pull only the parts this run needs.

        - instructions parts: the skill's core procedure, always loaded.
        - script parts: loaded only when the script's declared applicability
          overlaps the task text (language/tool keywords), never the whole
          skill by default.
        """
        task = ctx["task"]
        skill = ctx["skill"]
        parts = skill.get("parts", [])
        task_text = f"{task.prompt}\n{task.context}".lower()
        # Auxiliary instructions parts load only when relevant to this task
        # (measured cosine vs the task prompt); the core procedure (first
        # instructions part) always loads so the skill stays executable.
        part_thr = float(os.environ.get("PART_MATCH_THRESHOLD", 0.66))
        seen_core = False
        loaded = []
        for part in parts:
            if part.get("kind") == "instructions":
                if not seen_core:
                    loaded.append(part)
                    seen_core = True
                elif cosine(ctx["task_vec"], self.embedder.embed(part.get("content", "")[:600])) >= part_thr:
                    loaded.append(part)
            else:
                keywords = _script_keywords(part)
                if any(kw in task_text for kw in keywords):
                    loaded.append(part)
        if not loaded and parts:  # never route to a skill with nothing loaded
            loaded = [parts[0]]
        ctx["loaded_parts"] = loaded
        ctx["skill_parts_loaded"] = f"{len(loaded)} of {len(parts)}"
        return "run_skill"

    def _run_skill(self, ctx) -> str:
        result = run_skill(self.client, ctx["task"], ctx["skill"], ctx["loaded_parts"])
        ctx["result"] = result
        ctx["route"] = "skill"
        return "log_trace"

    def _no_hit(self, ctx) -> str:
        return "fallback_frontier"

    def _fallback_frontier(self, ctx) -> str:
        ctx["result"] = run_frontier(self.client, ctx["task"])
        ctx["route"] = "frontier"
        return "log_trace"

    def _log_trace(self, ctx) -> None:
        result = ctx["result"]
        skill = ctx.get("skill") if ctx["route"] == "skill" else None
        trace = make_trace(
            task_id=ctx["task"].id,
            route=ctx["route"],
            skill_id=skill["id"] if skill else None,
            skill_parts_loaded=ctx.get("skill_parts_loaded") if skill else None,
            decision_path=list(ctx["decision_path"]),
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            latency_ms=result.latency_ms,
            cost_usd=result.cost_usd,
            quality_score=None,  # filled by the judge when scored
        )
        trace["model"] = result.model
        if getattr(result, "fallback_from", None):
            # integrator-required: quota fallbacks are visible in the trace
            trace["fallback_from"] = result.fallback_from
        append_trace(trace, self.traces_file)
        if skill:
            record_hit(skill["id"], self.skills_dir)
        ctx["trace"] = trace
        return None


# Language/tool cues used to decide whether a script part applies to a task.
_SCRIPT_CUES = (
    "python", ".py", "pytest", "javascript", ".js", "typescript", ".ts",
    ".tsx", "node", "react", ".go", "rust", ".rs", "java", "c++", "css",
    "sql", "docker", "regex", "yaml", ".yml", "html", "bash", ".sh",
)


def _script_keywords(part: dict) -> list[str]:
    text = f" {part.get('id', '')} {part.get('content', '')} ".lower()
    return [cue for cue in _SCRIPT_CUES if cue in text]
