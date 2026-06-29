import asyncio
import os
from pathlib import Path

import pytest

import hillclimber
from hillclimber.models import Score

OCR_PROJECT = Path(__file__).parent / "ocr_example_project"


@pytest.mark.skipif(
    not os.environ.get("MISTRAL_API_KEY"),
    reason="end-to-end run scores the live OCR pipeline; needs MISTRAL_API_KEY",
)
def test_end_to_end():
    # v1 run: load the config, then score the baseline once with its scorer.
    baseline = asyncio.run(hillclimber.run(path=OCR_PROJECT))

    assert isinstance(baseline, Score)
    assert 0.0 <= baseline.value <= 1.0
