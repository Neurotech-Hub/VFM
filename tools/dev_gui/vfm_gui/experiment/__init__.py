"""
experiment — headless, event-driven experiment engine for VFM.

Users write short scripts (or pick a template) whose callbacks decide what
to do next in response to node events. The engine consumes the CAN event
stream via CanManager and issues commands; the GUI and JSON templates are
thin layers over this same API.
"""

from .events import EventKind, NodeEvent
from .context import ExperimentContext
from .runner import Experiment, ExperimentRunner
from .schema import ExperimentDef, ExperimentParam, load_experiment_defs, build_experiment
from .gui_controller import ExperimentController

__all__ = [
    "EventKind",
    "NodeEvent",
    "ExperimentContext",
    "Experiment",
    "ExperimentRunner",
    "ExperimentDef",
    "ExperimentParam",
    "load_experiment_defs",
    "build_experiment",
    "ExperimentController",
]
