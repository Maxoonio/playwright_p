import sys
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime
from threading import Lock

_LOGGER_LOCK = Lock()


@dataclass(frozen=True)
class LogContext:
    username: str
    profile: str


_LOG_CONTEXT: ContextVar[LogContext | None] = ContextVar(
    "test_drive_log_context",
    default=None,
)


def bind_log_context(*, username: str, profile: str) -> Token:
    """Binds one user/profile to every log emitted by the current async task."""

    return _LOG_CONTEXT.set(LogContext(username=username, profile=profile))


def reset_log_context(token: Token) -> None:
    _LOG_CONTEXT.reset(token)


def _safe_print(message: str) -> None:
    try:
        print(message, flush=True)
        return
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe_message = message.encode(
            encoding,
            errors="replace",
        ).decode(
            encoding,
            errors="replace",
        )
        print(safe_message, flush=True)


def log_step(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    context = _LOG_CONTEXT.get()
    if context is not None and not message.startswith(
        ("PASSED |", "FAILED |", "ERROR |", "SKIPPED |")
    ):
        message = f"{context.username} | {context.profile} | {message}"
    with _LOGGER_LOCK:
        _safe_print(f"[{timestamp}] {message}")


def log_block(message: str) -> None:
    with _LOGGER_LOCK:
        _safe_print(message)


def compact_exception_message(exc: Exception) -> str:
    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__

    for marker in ("Actual value:", "Call log:", "Aria snapshot:"):
        if marker in text:
            text = text.split(marker, maxsplit=1)[0].strip()

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0] if lines else exc.__class__.__name__
