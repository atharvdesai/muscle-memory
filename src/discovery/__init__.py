"""Discovery: offline batch that turns repeated frontier runs into skills.

Pipeline (decision-graph nodes, recorded in every GateDecision.decision_path):
    cluster_history -> propose_skill -> overlap_check -> judge_gate
        -> promote | merge | reject | pending_judge | pending_runner

Hard rules honored here:
  - Discovery never sees Task.type (loaders strip it; models lack the field).
  - The promotion gate (judge + measured savings + overlap_check) is never
    bypassed or faked. Missing judge/runner => candidate stays pending.
  - Skills are atomic (one responsibility) and non-overlapping.
  - All numbers come from measured runs.
"""
from .models import (
    Cluster,
    GateDecision,
    JudgeVerdict,
    RunResult,
    Skill,
    SkillPart,
    SkillStats,
    Task,
    Trace,
)
from .llm import BaseLLM, MockLLM, get_llm
from .embeddings import HashingEmbedder, OpenAIEmbedder, get_embedder
from .cluster import cluster_history, load_tasks, load_traces
from .propose import propose_skill
from .overlap import merge_skills, overlap_check
from .gate import judge_gate
from .discovery import DiscoveryReport, run_discovery

__all__ = [
    "Cluster",
    "GateDecision",
    "JudgeVerdict",
    "RunResult",
    "Skill",
    "SkillPart",
    "SkillStats",
    "Task",
    "Trace",
    "BaseLLM",
    "MockLLM",
    "get_llm",
    "HashingEmbedder",
    "OpenAIEmbedder",
    "get_embedder",
    "cluster_history",
    "load_tasks",
    "load_traces",
    "propose_skill",
    "merge_skills",
    "overlap_check",
    "judge_gate",
    "DiscoveryReport",
    "run_discovery",
]
