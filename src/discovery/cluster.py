"""cluster_history: find repeated workflows in the trace history.

Clusters prompt+context embeddings ONLY. Task.type (the corpus label) is
stripped at load time and never reaches this code - it exists only for
ground-truth eval by the proof agent.
"""
from __future__ import annotations

import json
import os
from typing import Iterable

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from .embeddings import cosine_similarity_matrix
from .models import Cluster, Task, Trace

_TASK_FIELDS = {"id", "prompt", "context", "expected"}


def load_tasks(path: str) -> list[Task]:
    """Load the corpus, stripping `type` so discovery can never see it.

    Accepts a JSON array file or JSONL (one task per line).
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        return []
    if raw.startswith("["):
        records = json.loads(raw)
    else:
        records = [json.loads(line) for line in raw.splitlines() if line.strip()]

    tasks = []
    for rec in records:
        rec = dict(rec)
        rec.pop("type", None)  # corpus label: ground truth only, never discovery input
        tasks.append(Task(**{k: v for k, v in rec.items() if k in _TASK_FIELDS}))
    return tasks


def load_traces(path: str) -> list[Trace]:
    """Load src/traces/runs.jsonl. Tolerates blank/garbled lines."""
    traces: list[Trace] = []
    if not os.path.exists(path):
        return traces
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                traces.append(Trace(**json.loads(line)))
            except Exception:
                continue  # never let one bad line kill the batch
    return traces


def _embed_text(task: Task, max_context_chars: int) -> str:
    return f"{task.prompt}\n\n{task.context[:max_context_chars]}"


def cluster_history(
    tasks: list[Task],
    traces: list[Trace],
    embedder,
    *,
    min_cluster_size: int = 3,
    distance_threshold: float = 0.35,
    max_context_chars: int = 1500,
) -> list[Cluster]:
    """Cluster frontier-routed task history into repeated workflows.

    Only tasks that actually burned frontier money (>= 1 frontier trace) are
    clustered: discovery exists to convert repeated frontier spend into
    skills. One-off control tasks never reach min_cluster_size inside a
    cluster (they stay singletons) and are filtered out - the control group
    stays unskilled by construction.

    Args:
        min_cluster_size: a workflow must repeat at least this many times to
            be skill-worthy.
        distance_threshold: cosine-distance merge threshold for agglomerative
            clustering (lower = tighter clusters).

    Returns:
        Clusters sorted by total measured frontier spend, most expensive
        workflow first.
    """
    frontier_cost: dict[str, float] = {}
    frontier_runs: dict[str, int] = {}
    for t in traces:
        if t.route == "frontier":
            frontier_cost[t.task_id] = frontier_cost.get(t.task_id, 0.0) + t.cost_usd
            frontier_runs[t.task_id] = frontier_runs.get(t.task_id, 0) + 1

    eligible = [t for t in tasks if t.id in frontier_cost]
    if len(eligible) < min_cluster_size:
        return []

    X = embedder.embed([_embed_text(t, max_context_chars) for t in eligible])
    clustering = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        metric="cosine",
        linkage="average",
    )
    labels = clustering.fit_predict(X)

    clusters: list[Cluster] = []
    grouped: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        grouped.setdefault(int(label), []).append(idx)

    for label, member_idx in sorted(grouped.items()):
        if len(member_idx) < min_cluster_size:
            continue  # one-offs stay unskilled (control group preserved)
        members = [eligible[i] for i in member_idx]
        vecs = X[member_idx]
        sims = cosine_similarity_matrix(vecs, vecs)
        n = len(member_idx)
        cohesion = (
            float((sims.sum() - n) / (n * (n - 1))) if n > 1 else 1.0
        )  # mean off-diagonal similarity
        total = sum(frontier_cost[t.id] for t in members)
        clusters.append(
            Cluster(
                id=f"cluster-{label:03d}",
                task_ids=[t.id for t in members],
                size=n,
                cohesion=round(cohesion, 4),
                mean_frontier_cost_usd=round(total / n, 6),
                total_frontier_cost_usd=round(total, 6),
            )
        )

    clusters.sort(key=lambda c: c.total_frontier_cost_usd, reverse=True)
    return clusters
