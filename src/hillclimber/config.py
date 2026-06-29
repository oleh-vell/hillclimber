"""Load ``hillclimber.toml`` into a :class:`~hillclimber.models.Config`.

The toml is the human-authored *intent* for an experiment (see README "File
model"). This is the single place that turns it into the validated config the
runner consumes; validation (and role-prompt/param defaulting) lives in the
model, so this stays a thin read-parse-validate step.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from hillclimber.models import Config

HILLCLIMBER_TOML = "hillclimber.toml"


def load_config(path: str | Path) -> Config:
    """Parse a ``hillclimber.toml`` into a :class:`Config`.

    Args:
        path: Path to a ``hillclimber.toml`` file, or a directory containing one.

    Returns:
        The validated :class:`Config`.

    Raises:
        FileNotFoundError: if the file (or the toml inside the directory) is missing.
        tomllib.TOMLDecodeError: if the file is not valid TOML.
        pydantic.ValidationError: if the contents don't satisfy the schema.
    """
    toml_path = Path(path)
    if toml_path.is_dir():
        toml_path = toml_path / HILLCLIMBER_TOML
    if not toml_path.is_file():
        raise FileNotFoundError(f"no {HILLCLIMBER_TOML} found at: {toml_path}")

    with toml_path.open("rb") as f:
        data = tomllib.load(f)
    return Config.model_validate(data)
