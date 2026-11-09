from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from threading import Lock
from uuid import uuid4

import settings
from models import CheckResult, CheckStatus
from playwright.async_api import Page
from utils.logging import log_block, log_step


class ResultCollector:
    def __init__(self, *, scenario_label: str) -> None:
        self._scenario_label = scenario_label
        self._results: list[CheckResult] = []
        self._lock = Lock()
        run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
        self._screenshot_directory = settings.FAILURE_SCREENSHOTS_DIR / run_id

    @property
    def results(self) -> list[CheckResult]:
        with self._lock:
            return list(self._results)

    @property
    def scenario_label(self) -> str:
        return self._scenario_label

    def record_pass(
        self,
        *,
        username: str,
        profile: str,
        action: str,
        message: str,
        section: int | None = None,
        step: int | None = None,
    ) -> None:
        self._record(
            CheckStatus.PASSED,
            username=username,
            profile=profile,
            section=section,
            step=step,
            action=action,
            message=message,
        )

    def record_fail(
        self,
        *,
        username: str,
        profile: str,
        action: str,
        message: str,
        section: int | None = None,
        step: int | None = None,
        screenshot: str | None = None,
    ) -> None:
        self._record(
            CheckStatus.FAILED,
            username=username,
            profile=profile,
            section=section,
            step=step,
            action=action,
            message=message,
            screenshot=screenshot,
        )

    def record_error(
        self,
        *,
        username: str,
        profile: str,
        action: str,
        message: str,
        section: int | None = None,
        step: int | None = None,
        screenshot: str | None = None,
    ) -> None:
        self._record(
            CheckStatus.ERROR,
            username=username,
            profile=profile,
            section=section,
            step=step,
            action=action,
            message=message,
            screenshot=screenshot,
        )

    def record_skip(
        self,
        *,
        username: str,
        profile: str,
        action: str,
        message: str,
        section: int | None = None,
        step: int | None = None,
        screenshot: str | None = None,
    ) -> None:
        self._record(
            CheckStatus.SKIPPED,
            username=username,
            profile=profile,
            section=section,
            step=step,
            action=action,
            message=message,
            screenshot=screenshot,
        )

    def _record(
        self,
        status: CheckStatus,
        *,
        username: str,
        profile: str,
        action: str,
        message: str,
        section: int | None,
        step: int | None,
        screenshot: str | None = None,
    ) -> None:
        result = CheckResult(
            username=username,
            profile=profile,
            section=section,
            step=step,
            action=action,
            status=status,
            message=message,
            screenshot=screenshot,
        )
        with self._lock:
            self._results.append(result)
        log_step(self.format_result(result))

    @staticmethod
    def format_result(result: CheckResult) -> str:
        section = (
            f"SECTION {result.section}" if result.section is not None else "SECTION -"
        )
        step = f"STEP {result.step}" if result.step is not None else "STEP -"
        return (
            f"{result.status.value.upper()} | {result.username} | "
            f"{result.profile} | {section} | {step} | "
            f"{result.action} | {result.message}"
        )

    async def capture_failure_screenshot(
        self,
        *,
        page: Page | None,
        username: str,
        action: str,
        status: str,
        section: int | None,
        step: int | None,
    ) -> str | None:
        if page is None or page.is_closed():
            return None

        self._screenshot_directory.mkdir(parents=True, exist_ok=True)
        parts = [
            datetime.now().strftime("%H%M%S-%f"),
            self._safe_file_part(status),
            self._safe_file_part(username),
            f"section-{section if section is not None else 'none'}",
            f"step-{step if step is not None else 'none'}",
            self._safe_file_part(action),
        ]
        path = self._screenshot_directory / ("_".join(parts) + ".png")
        try:
            await page.screenshot(
                path=str(path),
                full_page=True,
                timeout=10_000,
            )
        except Exception as exc:
            log_step(f"[REPORT] Не удалось снять скриншот: {exc}")
            return None

        resolved = str(path.resolve())
        log_step(f"[REPORT] Скриншот сохранен: {resolved}")
        return resolved

    @staticmethod
    def _safe_file_part(value: str) -> str:
        return re.sub(
            r"[^\w.-]+",
            "-",
            value,
            flags=re.UNICODE,
        ).strip("-._")

    def has_failures_or_errors(self) -> bool:
        return any(
            result.status in {CheckStatus.FAILED, CheckStatus.ERROR}
            for result in self.results
        )

    def get_summary(self) -> dict[str, int]:
        counts = Counter(result.status for result in self.results)
        return {
            "total": len(self.results),
            "passed": counts[CheckStatus.PASSED],
            "failed": counts[CheckStatus.FAILED],
            "errors": counts[CheckStatus.ERROR],
            "skipped": counts[CheckStatus.SKIPPED],
        }

    def render_text_summary(self) -> str:
        results = self.results
        summary = self.get_summary()
        users = {result.username for result in results}
        completed = {
            result.username
            for result in results
            if result.status == CheckStatus.PASSED
            and result.action == "PROFILE_COMPLETED"
        }

        lines = [
            "================ TEST-DRIVE SUMMARY ================",
            "",
            f"Test-drive:          {self._scenario_label}",
            f"Users processed:     {len(users)}",
            f"Profiles completed:  {len(completed)}",
            "",
            f"Checks total:        {summary['total']}",
            f"Passed:              {summary['passed']}",
            f"Failed:              {summary['failed']}",
            f"Errors:              {summary['errors']}",
            f"Skipped:             {summary['skipped']}",
        ]

        notable = [
            result
            for result in sorted(
                results,
                key=lambda item: (
                    item.username,
                    item.profile,
                    item.section if item.section is not None else -1,
                    item.step if item.step is not None else -1,
                    item.action,
                ),
            )
            if result.status
            in {
                CheckStatus.FAILED,
                CheckStatus.ERROR,
                CheckStatus.SKIPPED,
            }
        ]
        if notable:
            lines.extend(["", "Failures, errors and skipped:"])
            current_user: tuple[str, str] | None = None
            for result in notable:
                user_key = (result.username, result.profile)
                if user_key != current_user:
                    current_user = user_key
                    lines.extend(
                        [
                            "",
                            f"{result.username} | {result.profile}",
                        ]
                    )
                lines.append(self.format_result(result))

        return "\n".join(lines)

    def print_summary(self) -> None:
        log_block(self.render_text_summary())
