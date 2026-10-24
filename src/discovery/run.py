"""Module entry point so `python -m src.discovery.run` works.

Delegates to scripts/run_discovery (the canonical CLI). The demo
orchestrator invokes discovery this way.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import pathlib
import sys


def _load_script():
    script = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "run_discovery"
    loader = importlib.machinery.SourceFileLoader("run_discovery_script", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    raise SystemExit(_load_script().main())
