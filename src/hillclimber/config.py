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

    Paths in the toml are relative to the config file. The toml must sit at the
    artefact root: the scorer runs at each cycle's worktree root (a clone of the
    artefact), so anchoring paths to the config's directory means a ``cmd`` like
    ``uv run python eval.py`` resolves the same from the user's chair as from the
    worktree. ``path_to_artefact`` is resolved against the toml's directory (and
    defaults to it when omitted); the two must name the same directory.

    Args:
        path: Path to a ``hillclimber.toml`` file, or a directory containing one.

    Returns:
        The validated :class:`Config`, with ``path_to_artefact`` an absolute path.

    Raises:
        FileNotFoundError: if the file (or the toml inside the directory) is missing.
        tomllib.TOMLDecodeError: if the file is not valid TOML.
        ValueError: if the toml does not live at the artefact root.
        pydantic.ValidationError: if the contents don't satisfy the schema.
    """
    toml_path = Path(path)
    if toml_path.is_dir():
        toml_path = toml_path / HILLCLIMBER_TOML
    if not toml_path.is_file():
        raise FileNotFoundError(f"no {HILLCLIMBER_TOML} found at: {toml_path}")

    with toml_path.open("rb") as f:
        data = tomllib.load(f)

    # Resolve path_to_artefact relative to the toml (default: the toml's own dir)
    # and require it to be that same directory — the toml lives at the artefact
    # root, so the scorer's worktree-root cwd matches the config's location.
    toml_dir = toml_path.parent.resolve()
    artefact = Path(data.get("path_to_artefact", "."))
    if not artefact.is_absolute():
        artefact = toml_dir / artefact
    artefact = artefact.resolve()
    if artefact != toml_dir:
        raise ValueError(
            f"{HILLCLIMBER_TOML} must live at the artefact root, but path_to_artefact "
            f"resolves to {artefact} while the config is at {toml_dir}"
        )
    data["path_to_artefact"] = str(artefact)

    return Config.model_validate(data)
