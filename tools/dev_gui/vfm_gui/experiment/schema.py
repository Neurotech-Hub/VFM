"""
schema.py — JSON parameter schemas for experiment templates.

Each JSON file describes one experiment: which Python template to build,
plus the tunable parameters the GUI should render. Behavior stays in Python;
JSON only drives the parameter form.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .runner import Experiment

DEFAULT_EXPERIMENTS_DIR = Path(__file__).resolve().parents[2] / "experiments"

BuilderFn = Callable[..., Experiment]


@dataclass
class ExperimentParam:
    """One tunable parameter exposed in the GUI."""

    key: str
    label: str
    type: str  # int | float | bool | str | choice
    default: Any = None
    min: Optional[float] = None
    max: Optional[float] = None
    help: str = ""
    options: List[str] = field(default_factory=list)


@dataclass
class ExperimentDef:
    """Parsed experiment definition from a JSON schema file."""

    name: str
    label: str
    template: str
    description: str = ""
    parameters: List[ExperimentParam] = field(default_factory=list)
    source_path: Optional[Path] = None

    def defaults(self) -> Dict[str, Any]:
        return {p.key: p.default for p in self.parameters}


def _parse_param(raw: dict) -> ExperimentParam:
    ptype = str(raw.get("type", "str"))
    if ptype not in ("int", "float", "bool", "str", "choice"):
        raise ValueError(f"Unsupported parameter type: {ptype}")
    return ExperimentParam(
        key=str(raw["key"]),
        label=str(raw.get("label", raw["key"])),
        type=ptype,
        default=raw.get("default"),
        min=raw.get("min"),
        max=raw.get("max"),
        help=str(raw.get("help", "")),
        options=[str(o) for o in raw.get("options", [])],
    )


def load_experiment_def(path: Path) -> ExperimentDef:
    """Load a single experiment JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    params = [_parse_param(p) for p in data.get("parameters", [])]
    return ExperimentDef(
        name=str(data["name"]),
        label=str(data.get("label", data["name"])),
        template=str(data.get("template", data["name"])),
        description=str(data.get("description", "")),
        parameters=params,
        source_path=path,
    )


def load_experiment_defs(
    directory: Optional[Path | str] = None,
) -> List[ExperimentDef]:
    """Load all ``*.json`` experiment defs from a directory (sorted by name)."""
    root = Path(directory) if directory else DEFAULT_EXPERIMENTS_DIR
    if not root.is_dir():
        return []
    defs: List[ExperimentDef] = []
    for path in sorted(root.glob("*.json")):
        defs.append(load_experiment_def(path))
    return defs


def resolve_builder(template: str) -> BuilderFn:
    """
    Resolve a template name to a ``build(**kwargs) -> Experiment`` callable.

    Looks up builtins first (``free_feeding``), then
    ``vfm_gui.experiment.templates.<name>.build``.
    """
    from .templates import free_feeding as free_feeding_build

    builtins: Dict[str, BuilderFn] = {
        "free_feeding": free_feeding_build,
    }
    if template in builtins:
        return builtins[template]

    mod = importlib.import_module(f"vfm_gui.experiment.templates.{template}")
    if not hasattr(mod, "build") or not callable(mod.build):
        raise ImportError(f"Template '{template}' has no build() factory")
    return mod.build


def coerce_param_value(param: ExperimentParam, value: Any) -> Any:
    """Coerce a GUI/widget value to the parameter's declared type."""
    if param.type == "int":
        return int(value) if value is not None else 0
    if param.type == "float":
        return float(value) if value is not None else 0.0
    if param.type == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if param.type == "choice":
        s = str(value)
        if param.options and s not in param.options:
            return param.default if param.default in param.options else param.options[0]
        return s
    return "" if value is None else str(value)


def build_experiment(
    exp_def: ExperimentDef,
    params: Optional[Dict[str, Any]] = None,
    nodes: Optional[Sequence[int]] = None,
) -> Experiment:
    """
    Build an Experiment from a schema def + user parameter values.

    Special mappings for free_feeding-style templates:
      - ``max_pellets == 0`` → omitted (no pellet cap)
      - ``minutes`` / ``hours`` / ``seconds`` passed through as-is
    """
    values = exp_def.defaults()
    if params:
        for p in exp_def.parameters:
            if p.key in params:
                values[p.key] = coerce_param_value(p, params[p.key])

    # 0 pellet cap means "no limit"
    if "max_pellets" in values and (values["max_pellets"] is None or int(values["max_pellets"]) <= 0):
        values["max_pellets"] = None

    kwargs: Dict[str, Any] = dict(values)
    if nodes is not None:
        kwargs["nodes"] = list(nodes)

    builder = resolve_builder(exp_def.template)
    return builder(**kwargs)
