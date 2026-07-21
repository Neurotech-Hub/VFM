"""
templates — built-in experiment templates.

Each template is a factory that returns a configured Experiment.
"""

from .free_feeding import build as free_feeding
from .free_feeding import build
from .fixed_and_random import build as fixed_and_random
from .probability_delivery import build as probability_delivery

__all__ = ["build", "free_feeding", "fixed_and_random", "probability_delivery"]
