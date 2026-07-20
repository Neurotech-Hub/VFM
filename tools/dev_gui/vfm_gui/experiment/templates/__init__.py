"""
templates — built-in experiment templates.

Each template is a factory that returns a configured Experiment.
"""

from .free_feeding import build as free_feeding
from .free_feeding import build

__all__ = ["build", "free_feeding"]
