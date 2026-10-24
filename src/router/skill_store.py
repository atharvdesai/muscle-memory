"""Read/write access to the skill web: one JSON file per skill in src/skills/.

Schema (frozen, see INTERFACE.md):
  {id, name, responsibility, trigger_description,
   parts: [{id, kind: "instructions"|"script", content}],
   created_at, stats: {hits, avg_quality, savings_usd}}
"""
from __future__ import annotations

import json
import pathlib

SKILLS_DIR = pathlib.Path(__file__).resolve().parents[1] / "skills"


def list_skills(skills_dir: pathlib.Path | None = None) -> list[dict]:
    d = skills_dir or SKILLS_DIR
    skills = []
    if not d.exists():
        return skills
    for path in sorted(d.glob("*.json")):
        skills.append(json.loads(path.read_text()))
    return skills


def load_skill(skill_id: str, skills_dir: pathlib.Path | None = None) -> dict:
    path = (skills_dir or SKILLS_DIR) / f"{skill_id}.json"
    return json.loads(path.read_text())


def save_skill(skill: dict, skills_dir: pathlib.Path | None = None) -> pathlib.Path:
    d = skills_dir or SKILLS_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{skill['id']}.json"
    path.write_text(json.dumps(skill, indent=2) + "\n")
    return path


def record_hit(skill_id: str, skills_dir: pathlib.Path | None = None) -> None:
    """Increment the hit counter on a routed skill run.

    avg_quality and savings_usd are owned by the judge/discovery slice
    (they come out of the promotion gate), so they are not touched here.
    """
    d = skills_dir or SKILLS_DIR
    path = d / f"{skill_id}.json"
    if not path.exists():
        return
    skill = json.loads(path.read_text())
    skill.setdefault("stats", {})
    skill["stats"]["hits"] = int(skill["stats"].get("hits") or 0) + 1
    path.write_text(json.dumps(skill, indent=2) + "\n")
