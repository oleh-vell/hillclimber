"""The strategy interface.

A strategy is the *how* of the climb: given a validated ``Config``, it decides
how runs are produced and orchestrated (iteratively, as a chain, etc.) and
drives them to completion. ``Config.strategy`` names which one to use; the
runner (see ``hillclimber.run``) picks the matching subclass and calls
``execute``.

Subclasses (e.g. ``chain``) implement the loop; this base only fixes the
contract so the runner stays agnostic to which strategy it is driving.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from hillclimber.models import Config, ExperimentStatus, Score


class Strategy(ABC):
    """Base class for climb strategies. One method: ``execute``."""

    @abstractmethod
    async def execute(self, config: Config, baseline: Score) -> ExperimentStatus:
        """Drive the climb described by ``config`` to completion.

        Args:
            config: The validated experiment config (see ``hillclimber.models``).
            baseline: The artefact's baseline ``Score``, scored once before any
                cycle — the number each run must beat.

        Returns:
            The final ``ExperimentStatus`` — runs attempted and the best so far.
        """
        ...
