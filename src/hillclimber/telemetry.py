"""Logging setup for hillclimber.

Two goals, in order:

1. **See what's going on.** A single call to :func:`configure_logging` wires the
   project's loggers to a console handler so a run narrates itself. Modules log
   through ``get_logger(__name__)``; the library never configures handlers on
   import (it only installs a :class:`~logging.NullHandler`, the standard library
   convention), so importing hillclimber stays quiet until an entry point opts in.

2. **Send logs anywhere via OTEL.** Export is opt-in and vendor-neutral: when
   enabled, the same log records are bridged onto the OpenTelemetry logs SDK and
   shipped over OTLP. Where they land is the standard ``OTEL_*`` environment
   (``OTEL_EXPORTER_OTLP_ENDPOINT`` etc.), so folks point it at whatever
   OTEL-compatible platform they run — nothing here is tied to one vendor. The
   OTEL packages are an optional dependency (``pip install hillclimber[otel]``);
   without them, console logging works unchanged.

The OTEL exporter ships records from a background thread (a batching processor),
so it never blocks the event loop (see CLAUDE.md "Concurrency").

Typical use from a synchronous entry point::

    from hillclimber import configure_logging, run

    configure_logging()              # console only
    asyncio.run(run("path/to/experiment"))

Enable OTEL export with ``configure_logging(enable_otel=True)`` or by setting
``HILLCLIMBER_OTEL=1`` (or any ``OTEL_EXPORTER_OTLP_ENDPOINT``) in the env.
"""

from __future__ import annotations

import importlib
import logging
import os

# The project's top-level package(s). ``configure_logging`` attaches handlers to
# exactly these, so hillclimber's own logs surface without capturing (or muting)
# logs from third-party libraries a consumer may also be using. Public so a
# consumer that temporarily re-routes the project's logs (the CLI's live
# dashboard) targets the same set.
PACKAGE_LOGGERS = ("hillclimber",)

# Marks handlers this module installed, so re-running ``configure_logging`` is
# idempotent: it replaces its own handlers rather than stacking duplicates and
# never touches handlers a consumer added themselves.
_HC_HANDLER_FLAG = "_hillclimber_managed"

_DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# OTEL service name default; ``OTEL_SERVICE_NAME`` in the env still wins.
_SERVICE_NAME = "hillclimber"


def get_logger(name: str) -> logging.Logger:
    """Return the logger for ``name`` (call as ``get_logger(__name__)``)."""
    return logging.getLogger(name)


def _resolve_level(level: int | str | None) -> int:
    """Resolve an explicit level, the ``HILLCLIMBER_LOG_LEVEL`` env, then ``INFO``.

    A string (``"DEBUG"``, ``"info"``, ...) or an int (``logging.DEBUG``) is
    accepted; an unrecognised name falls back to ``INFO`` rather than raising.
    """
    if level is None:
        level = os.environ.get("HILLCLIMBER_LOG_LEVEL")
    if level is None:
        return logging.INFO
    if isinstance(level, int):
        return level
    resolved = logging.getLevelNamesMapping().get(level.upper())
    return resolved if resolved is not None else logging.INFO


def _otel_requested(enable_otel: bool | None) -> bool:
    """Decide whether to wire OTEL export.

    Explicit ``enable_otel`` wins. Left unset, export turns on when the env asks
    for it — ``HILLCLIMBER_OTEL`` truthy, or any ``OTEL_EXPORTER_OTLP_ENDPOINT``
    configured — so a deployment can opt in without touching the call site.
    """
    if enable_otel is not None:
        return enable_otel
    if os.environ.get("HILLCLIMBER_OTEL", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def _build_otel_handler(level: int) -> logging.Handler:
    """Bridge stdlib logging onto the OpenTelemetry logs SDK over OTLP.

    Imported lazily through :mod:`importlib` so the OTEL packages stay a genuine
    optional dependency: nothing here is referenced unless export is enabled.

    Raises:
        RuntimeError: If the optional OTEL packages are not installed.
    """
    try:
        sdk_logs = importlib.import_module("opentelemetry.sdk._logs")
        sdk_logs_export = importlib.import_module("opentelemetry.sdk._logs.export")
        otlp_log_exporter = importlib.import_module("opentelemetry.exporter.otlp.proto.http._log_exporter")
        resources = importlib.import_module("opentelemetry.sdk.resources")
    except ImportError as exc:
        raise RuntimeError(
            "OTEL logging was requested but the OpenTelemetry packages are not installed. "
            "Install them with: pip install 'hillclimber[otel]'"
        ) from exc

    # Endpoint, headers, protocol etc. are read from the standard OTEL_* env by
    # the exporter itself, so any OTEL-compatible backend works without code here.
    resource = resources.Resource.create({"service.name": _SERVICE_NAME})
    provider = sdk_logs.LoggerProvider(resource=resource)
    provider.add_log_record_processor(sdk_logs_export.BatchLogRecordProcessor(otlp_log_exporter.OTLPLogExporter()))
    return sdk_logs.LoggingHandler(level=level, logger_provider=provider)


def configure_logging(
    level: int | str | None = None,
    *,
    enable_otel: bool | None = None,
    fmt: str = _DEFAULT_FORMAT,
) -> None:
    """Wire hillclimber's loggers to a console handler (and optionally OTEL).

    Idempotent: it removes any handlers it installed on a previous call before
    adding fresh ones, so calling it more than once never duplicates output and
    never disturbs handlers a consumer attached themselves. Safe to call from a
    synchronous entry point before ``asyncio.run`` drives the async core.

    Args:
        level: Log level as an int (``logging.DEBUG``) or name (``"DEBUG"``).
            Defaults to ``HILLCLIMBER_LOG_LEVEL`` in the env, then ``INFO``.
        enable_otel: Force OTEL export on (``True``) or off (``False``). Left as
            ``None``, export turns on when the env asks for it (see
            ``HILLCLIMBER_OTEL`` / ``OTEL_EXPORTER_OTLP_ENDPOINT``).
        fmt: The console handler's :class:`logging.Formatter` format string.

    Raises:
        RuntimeError: If OTEL export is enabled but its packages are not installed.
    """
    resolved_level = _resolve_level(level)

    handlers: list[logging.Handler] = []

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(fmt))
    handlers.append(console)

    if _otel_requested(enable_otel):
        handlers.append(_build_otel_handler(resolved_level))

    for handler in handlers:
        # Tag so the next call can find and remove exactly these.
        handler.set_name(_HC_HANDLER_FLAG)
        setattr(handler, _HC_HANDLER_FLAG, True)

    for name in PACKAGE_LOGGERS:
        package_logger = logging.getLogger(name)
        # Drop handlers we installed previously; leave any others in place.
        for existing in list(package_logger.handlers):
            if getattr(existing, _HC_HANDLER_FLAG, False):
                package_logger.removeHandler(existing)
        for handler in handlers:
            package_logger.addHandler(handler)
        package_logger.setLevel(resolved_level)
        # These are top-level app loggers; don't also bubble to the root logger's
        # handlers (e.g. a consumer's ``logging.basicConfig``) and double-print.
        package_logger.propagate = False


# Library convention: attach a no-op handler to each package logger at import so
# records never trigger Python's "No handlers could be found" warning before an
# entry point calls ``configure_logging``. These are inert and idempotent.
for _name in PACKAGE_LOGGERS:
    logging.getLogger(_name).addHandler(logging.NullHandler())
