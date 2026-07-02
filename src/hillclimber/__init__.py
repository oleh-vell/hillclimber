"""Hillclimber: an opinionated CLI that auto-improves code artefacts with LLMs."""

from hillclimber.config import load_config
from hillclimber.models import Config, Eval
from hillclimber.progress import RunEvent, RunEventSink
from hillclimber.run import get_baseline_score, run
from hillclimber.scoring import ScorerError
from hillclimber.telemetry import configure_logging, get_logger

__all__ = [
    "Config",
    "Eval",
    "RunEvent",
    "RunEventSink",
    "ScorerError",
    "configure_logging",
    "get_baseline_score",
    "get_logger",
    "load_config",
    "run",
]
