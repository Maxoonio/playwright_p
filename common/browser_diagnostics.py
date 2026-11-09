from __future__ import annotations

import asyncio
import os
import re
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from playwright.async_api import BrowserContext, Page
from utils.logging import compact_exception_message, log_step


class BrowserDiagnostics:
    """Опциональные периодические PNG и Playwright trace для live-прогона."""

    DIRECTORY_ENV = "PT_TEST_DRIVE_DIAGNOSTICS_DIR"
    INTERVAL_ENV = "PT_TEST_DRIVE_DIAGNOSTIC_SCREENSHOT_INTERVAL_SECONDS"
    DEFAULT_INTERVAL_SECONDS = 10.0

    def __init__(
        self,
        *,
        context: BrowserContext,
        username: str,
        directory: Path,
        interval_seconds: float,
    ) -> None:
        self._context = context
        self._username = username
        self._directory = directory
        self._screenshots_directory = directory / "screenshots"
        self._interval_seconds = interval_seconds
        self._capture_lock = asyncio.Lock()
        self._capture_number = 0
        self._capture_task: asyncio.Task[None] | None = None
        self._trace_started = False

    @classmethod
    async def start(
        cls,
        *,
        context: BrowserContext,
        username: str,
    ) -> BrowserDiagnostics | None:
        configured_directory = os.getenv(cls.DIRECTORY_ENV, "").strip()
        if not configured_directory:
            return None

        root = Path(configured_directory).expanduser()
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
        user_directory = root / cls._safe_file_part(username)
        user_directory.mkdir(parents=True, exist_ok=True)

        interval_seconds = cls._read_interval_seconds()
        diagnostics = cls(
            context=context,
            username=username,
            directory=user_directory,
            interval_seconds=interval_seconds,
        )
        await context.tracing.start(
            screenshots=True,
            snapshots=True,
            sources=True,
        )
        diagnostics._trace_started = True
        await diagnostics.capture("browser-started")
        diagnostics._capture_task = asyncio.create_task(
            diagnostics._capture_periodically(),
            name=f"test-drive-diagnostics-{username}",
        )
        log_step(
            f"[DIAGNOSTICS][{username}] PNG каждые "
            f"{interval_seconds:g} сек. и DOM trace: {user_directory}"
        )
        return diagnostics

    @classmethod
    def _read_interval_seconds(cls) -> float:
        raw_value = os.getenv(
            cls.INTERVAL_ENV,
            str(cls.DEFAULT_INTERVAL_SECONDS),
        ).strip()
        try:
            interval_seconds = float(raw_value)
        except ValueError:
            interval_seconds = cls.DEFAULT_INTERVAL_SECONDS
        return max(interval_seconds, 1.0)

    async def _capture_periodically(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval_seconds)
                await self.capture("periodic")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_step(
                f"[DIAGNOSTICS][{self._username}] Периодический снимок "
                f"остановлен: {compact_exception_message(exc)}"
            )

    async def capture(self, label: str) -> None:
        async with self._capture_lock:
            pages = tuple(self._context.pages)
            if not pages:
                return
            self._screenshots_directory.mkdir(parents=True, exist_ok=True)
            self._capture_number += 1
            capture_number = self._capture_number
            timestamp = datetime.now().strftime("%H%M%S-%f")
            safe_label = self._safe_file_part(label)

            for page_index, page in enumerate(pages, start=1):
                await self._capture_page(
                    page=page,
                    page_index=page_index,
                    capture_number=capture_number,
                    timestamp=timestamp,
                    label=safe_label,
                )

    async def _capture_page(
        self,
        *,
        page: Page,
        page_index: int,
        capture_number: int,
        timestamp: str,
        label: str,
    ) -> None:
        if page.is_closed():
            return
        filename = f"{capture_number:05d}_{timestamp}_{label}_page-{page_index}.png"
        try:
            await page.screenshot(
                path=str(self._screenshots_directory / filename),
                full_page=False,
                animations="disabled",
                timeout=10_000,
            )
        except Exception as exc:
            log_step(
                f"[DIAGNOSTICS][{self._username}] Не удалось снять "
                f"page-{page_index}: {compact_exception_message(exc)}"
            )

    async def stop(self) -> None:
        if self._capture_task is not None:
            self._capture_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._capture_task
            self._capture_task = None

        try:
            await self.capture("browser-finished")
        except Exception as exc:
            log_step(
                f"[DIAGNOSTICS][{self._username}] Финальный PNG не снят: "
                f"{compact_exception_message(exc)}"
            )

        if self._trace_started:
            try:
                await self._context.tracing.stop(
                    path=str(self._directory / "trace.zip")
                )
            except Exception as exc:
                log_step(
                    f"[DIAGNOSTICS][{self._username}] Trace не сохранен: "
                    f"{compact_exception_message(exc)}"
                )
            finally:
                self._trace_started = False

        log_step(
            f"[DIAGNOSTICS][{self._username}] Диагностика завершена: {self._directory}"
        )

    @staticmethod
    def _safe_file_part(value: str) -> str:
        return (
            re.sub(
                r"[^\w.-]+",
                "-",
                value,
                flags=re.UNICODE,
            ).strip("-._")
            or "unknown"
        )
