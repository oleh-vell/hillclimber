import importlib
import logging

import pytest

# Import the package first so it initialises cleanly (see test_git_utils).
import hillclimber  # noqa: F401
from hillclimber import telemetry


@pytest.fixture(autouse=True)
def _restore_logging():
    """Snapshot and restore the package loggers' state around each test.

    ``configure_logging`` mutates global logging state (handlers, level,
    propagate); restore it so tests don't leak into one another.
    """
    saved = {}
    for name in telemetry._PACKAGE_LOGGERS:
        lg = logging.getLogger(name)
        saved[name] = (list(lg.handlers), lg.level, lg.propagate)
    try:
        yield
    finally:
        for name, (handlers, level, propagate) in saved.items():
            lg = logging.getLogger(name)
            lg.handlers = handlers
            lg.setLevel(level)
            lg.propagate = propagate


def _managed_handlers(name: str) -> list[logging.Handler]:
    lg = logging.getLogger(name)
    return [h for h in lg.handlers if getattr(h, telemetry._HC_HANDLER_FLAG, False)]


# --------------------------------------------------------------------------- #
# get_logger
# --------------------------------------------------------------------------- #


def test_get_logger_returns_named_logger():
    assert telemetry.get_logger("hillclimber.run").name == "hillclimber.run"


# --------------------------------------------------------------------------- #
# configure_logging
# --------------------------------------------------------------------------- #


def test_configure_logging_attaches_console_handler_to_each_package():
    telemetry.configure_logging(level="DEBUG")

    for name in telemetry._PACKAGE_LOGGERS:
        managed = _managed_handlers(name)
        assert len(managed) == 1
        assert isinstance(managed[0], logging.StreamHandler)
        assert logging.getLogger(name).level == logging.DEBUG


def test_configure_logging_is_idempotent():
    telemetry.configure_logging()
    telemetry.configure_logging()
    telemetry.configure_logging()

    # Re-running replaces its own handlers rather than stacking duplicates.
    for name in telemetry._PACKAGE_LOGGERS:
        assert len(_managed_handlers(name)) == 1


def test_configure_logging_leaves_foreign_handlers_alone():
    lg = logging.getLogger("hillclimber")
    foreign = logging.NullHandler()
    lg.addHandler(foreign)

    telemetry.configure_logging()
    telemetry.configure_logging()

    assert foreign in lg.handlers


def test_records_reach_the_configured_handler():
    telemetry.configure_logging(level="INFO")

    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    # Attach to the package root; a child logger's record propagates to it.
    logging.getLogger("hillclimber").addHandler(_Capture())
    telemetry.get_logger("hillclimber.run").info("hello %s", "world")

    assert any(r.getMessage() == "hello world" for r in captured)


# --------------------------------------------------------------------------- #
# level resolution
# --------------------------------------------------------------------------- #


def test_level_defaults_to_info(monkeypatch):
    monkeypatch.delenv("HILLCLIMBER_LOG_LEVEL", raising=False)
    assert telemetry._resolve_level(None) == logging.INFO


def test_level_reads_env(monkeypatch):
    monkeypatch.setenv("HILLCLIMBER_LOG_LEVEL", "warning")
    assert telemetry._resolve_level(None) == logging.WARNING


def test_explicit_level_wins_over_env(monkeypatch):
    monkeypatch.setenv("HILLCLIMBER_LOG_LEVEL", "WARNING")
    assert telemetry._resolve_level("DEBUG") == logging.DEBUG
    assert telemetry._resolve_level(logging.ERROR) == logging.ERROR


def test_unknown_level_falls_back_to_info(monkeypatch):
    monkeypatch.delenv("HILLCLIMBER_LOG_LEVEL", raising=False)
    assert telemetry._resolve_level("NONSENSE") == logging.INFO


# --------------------------------------------------------------------------- #
# OTEL opt-in
# --------------------------------------------------------------------------- #


def test_otel_off_by_default(monkeypatch):
    monkeypatch.delenv("HILLCLIMBER_OTEL", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert telemetry._otel_requested(None) is False


def test_explicit_flag_overrides_env(monkeypatch):
    monkeypatch.setenv("HILLCLIMBER_OTEL", "1")
    assert telemetry._otel_requested(False) is False
    monkeypatch.delenv("HILLCLIMBER_OTEL", raising=False)
    assert telemetry._otel_requested(True) is True


def test_env_flag_enables_otel(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("HILLCLIMBER_OTEL", "true")
    assert telemetry._otel_requested(None) is True


def test_otel_endpoint_enables_otel(monkeypatch):
    monkeypatch.delenv("HILLCLIMBER_OTEL", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    assert telemetry._otel_requested(None) is True


def test_enable_otel_without_packages_raises():
    # The optional OTEL packages are not installed in the dev environment, so
    # requesting export must fail loudly with an actionable message. Probe the
    # actual SDK submodule, not the ``opentelemetry`` namespace package (which can
    # be present transitively without the SDK).
    try:
        importlib.import_module("opentelemetry.sdk._logs")
    except ImportError:
        with pytest.raises(RuntimeError, match="hillclimber\\[otel\\]"):
            telemetry.configure_logging(enable_otel=True)
    else:  # pragma: no cover - only when the extra happens to be installed
        pytest.skip("opentelemetry SDK is installed; the missing-package path can't run")
