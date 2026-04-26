"""Scenario YAML loader with extends / deep-merge support."""

import copy
from pathlib import Path

import yaml
from pydantic import ValidationError

from controller.atf_ctrl.scenarios.models import Scenario

_SCENARIOS_DIR = Path(__file__).parent.parent.parent.parent / "scenarios"


def load(path: str | Path) -> Scenario:
    """Load a scenario YAML file, merging any base file declared via `extends`.

    Raises:
        FileNotFoundError: if the scenario or base file doesn't exist.
        ValueError: if the YAML fails Pydantic validation.
    """
    path = Path(path)
    if not path.is_absolute():
        path = _SCENARIOS_DIR / path

    raw = _read_yaml(path)

    if extends := raw.pop("extends", None):
        base_path = _SCENARIOS_DIR / extends
        base = _read_yaml(base_path)
        base.pop("extends", None)
        raw = _deep_merge(base, raw)

    try:
        return Scenario.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"Invalid scenario '{path.name}':\n{e}") from e


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Lists are replaced, not appended."""
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result
