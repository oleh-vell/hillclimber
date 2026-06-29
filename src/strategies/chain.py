"""The ``chain`` strategy.

Chains cycles one after another: each run is attempted in sequence and folded
into the running ``ExperimentStatus``. v1 is the thinnest slice — it establishes
the baseline status that later cycles will accumulate into (see README "Core
loop" / "Architecture seam"). The per-cycle mutation loop attaches here.
"""

from __future__ import annotations

from hillclimber.models import Config, ExperimentStatus, Score
from strategies.base import Strategy


class Chain(Strategy):
    """Run cycles in sequence, recording the best so far."""

    async def execute(self, config: Config, baseline: Score) -> ExperimentStatus:
        """Drive the chained climb to completion.

        Args:
            config: The validated experiment config.
            baseline: The baseline ``Score`` each run must beat.

        Returns:
            The final ``ExperimentStatus``. v1 reports the baseline with no runs
            yet attempted; the cycle loop fills ``runs``/``best`` later.
        """
        return ExperimentStatus(
            baseline_score=baseline,
            runs=[],
            best=None,
            completed=0,
            total=config.budget.cycles,
        )
