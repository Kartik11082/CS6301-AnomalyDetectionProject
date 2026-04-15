from __future__ import annotations

import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file as a dictionary."""
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def ensure_dirs(paths: Iterable[str | Path]) -> None:
    """Create directories only when they do not already exist."""
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    """Write stable JSON output for logs and reports."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)


def _utc_now_iso() -> str:
    """Return an ISO UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_run_manifest(
    seed: int,
    data_config_path: str | Path,
    neo4j_config_path: str | Path,
    model_config_path: str | Path,
    enabled_stages: list[str],
) -> dict[str, Any]:
    """Capture execution metadata so runs are reproducible."""
    return {
        "timestamp_utc": _utc_now_iso(),
        "python_version": platform.python_version(),
        "seed": seed,
        "config_paths": {
            "data": str(data_config_path),
            "neo4j": str(neo4j_config_path),
            "model": str(model_config_path),
        },
        "enabled_stages": enabled_stages,
    }

