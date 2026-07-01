"""Subcommands, one module per command.

Each module defines a plain function (with Typer ``Annotated`` options in its
signature) and is registered onto the app in :mod:`hillclimber.cli.app`. The
modules never import the app, so command definitions and app wiring stay
decoupled and free of import cycles.
"""
