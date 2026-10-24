"""Decision-graph renderer.

Hand-laid-out SVG of the router's decision graph (no graphviz dependency).
Given one run's `decision_path`, the nodes that fired and the edges between
consecutive fired nodes are highlighted - the "show me exactly how this run
was decided" beat of the demo.

Pure string generation; `render_graph` returns a self-contained SVG string.
"""

from __future__ import annotations

import html

W, H = 1040, 560
NODE_W, NODE_H = 150, 44

# name -> (cx, cy, kind). kind: "live" (online routing) or "offline" (discovery).
_NODES = {
    "embed_task":        (90,  180, "live"),
    "match_skills":      (250, 180, "live"),
    "skill_hit":         (420, 90,  "live"),
    "load_parts":        (590, 90,  "live"),
    "run_skill":         (760, 90,  "live"),
    "no_hit":            (420, 270, "live"),
    "fallback_frontier": (590, 270, "live"),
    "log_trace":         (900, 180, "live"),
    "cluster_history":   (170, 470, "offline"),
    "propose_skill":     (340, 470, "offline"),
    "overlap_check":     (510, 470, "offline"),
    "judge_gate":        (680, 470, "offline"),
    "promote":           (860, 420, "offline"),
    "merge":             (860, 470, "offline"),
    "reject":            (860, 520, "offline"),
}

_EDGES = [
    ("embed_task", "match_skills"),
    ("match_skills", "skill_hit"),
    ("match_skills", "no_hit"),
    ("skill_hit", "load_parts"),
    ("load_parts", "run_skill"),
    ("run_skill", "log_trace"),
    ("no_hit", "fallback_frontier"),
    ("fallback_frontier", "log_trace"),
    ("cluster_history", "propose_skill"),
    ("propose_skill", "overlap_check"),
    ("overlap_check", "judge_gate"),
    ("judge_gate", "promote"),
    ("judge_gate", "reject"),
    ("overlap_check", "merge"),
]

C_BASE_FILL, C_BASE_STROKE = "#1e2130", "#8b93b0"
C_HIT_FILL, C_HIT_STROKE = "#f59e0b", "#ffd166"
C_EDGE_BASE, C_EDGE_HIT = "#3a3f55", "#f59e0b"
C_TEXT, C_TEXT_HIT = "#c9cee3", "#111111"


def _rect(name: str, cx: int, cy: int, kind: str, hit: bool) -> str:
    x, y = cx - NODE_W // 2, cy - NODE_H // 2
    fill = C_HIT_FILL if hit else C_BASE_FILL
    stroke = C_HIT_STROKE if hit else C_BASE_STROKE
    dash = ' stroke-dasharray="6 4"' if kind == "offline" and not hit else ""
    txt_fill = C_TEXT_HIT if hit else C_TEXT
    return (
        f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" rx="10" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}/>'
        f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" font-family="monospace" '
        f'font-size="14" fill="{txt_fill}">{html.escape(name)}</text>'
    )


def _edge(a: str, b: str, hit: bool) -> str:
    ax, ay, _ = _NODES[a]
    bx, by, _ = _NODES[b]
    # Trim to node boxes (rough but clean at this layout's separations).
    dx, dy = bx - ax, by - ay
    if abs(dx) > abs(dy):
        ax += NODE_W // 2 * (1 if dx > 0 else -1)
        bx -= NODE_W // 2 * (1 if dx > 0 else -1)
    else:
        ay += NODE_H // 2 * (1 if dy > 0 else -1)
        by -= NODE_H // 2 * (1 if dy > 0 else -1)
    color = C_EDGE_HIT if hit else C_EDGE_BASE
    width = 3.5 if hit else 1.5
    marker = "url(#arrow-hit)" if hit else "url(#arrow)"
    return (f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" stroke="{color}" '
            f'stroke-width="{width}" marker-end="{marker}"/>')


def render_graph(decision_path: list[str] | None = None, title: str = "") -> str:
    """SVG string of the decision graph; `decision_path` nodes/edges highlighted."""
    path = list(decision_path or [])
    path_set = set(path)
    edge_hits = {(path[i], path[i + 1]) for i in range(len(path) - 1)}

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="100%" style="background:#12141f;border-radius:12px">',
        "<defs>"
        f'<marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">'
        f'<path d="M0,0 L7,3 L0,6 Z" fill="{C_EDGE_BASE}"/></marker>'
        f'<marker id="arrow-hit" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">'
        f'<path d="M0,0 L7,3 L0,6 Z" fill="{C_EDGE_HIT}"/></marker>'
        "</defs>",
    ]
    if title:
        parts.append(f'<text x="20" y="30" font-family="monospace" font-size="15" '
                     f'fill="{C_TEXT}">{html.escape(title)}</text>')
    parts.append(f'<text x="20" y="445" font-family="monospace" font-size="12" '
                 f'fill="{C_BASE_STROKE}">offline discovery (dashed)</text>')
    for a, b in _EDGES:
        parts.append(_edge(a, b, (a, b) in edge_hits))
    for name, (cx, cy, kind) in _NODES.items():
        parts.append(_rect(name, cx, cy, kind, name in path_set))
    parts.append("</svg>")
    return "".join(parts)
