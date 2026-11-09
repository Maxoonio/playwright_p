from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Iterator
from uuid import uuid4

import pytest
import settings
from models import CheckResult, CheckStatus
from utils.logging import log_step

if TYPE_CHECKING:
    from results import ResultCollector

try:
    import allure
    from allure_commons import plugin_manager
except ImportError:
    allure = None
    plugin_manager = None


STATUS_LABELS: dict[CheckStatus, tuple[str, str]] = {
    CheckStatus.PASSED: ("✅", "PASSED"),
    CheckStatus.FAILED: ("❌", "FAILED"),
    CheckStatus.ERROR: ("💥", "ERROR"),
    CheckStatus.SKIPPED: ("⏭", "SKIPPED"),
}


def configure_allure_test_drive() -> None:
    if not _is_available():
        return

    try:
        allure.dynamic.title(
            f"{settings.PRODUCT_NAME} · test-drive {settings.BASIC_CASE_ID}"
        )
        allure.dynamic.description(
            f"Проверка {settings.PRODUCT_NAME} test-drive "
            f"{settings.BASIC_CASE_ID} всеми включенными "
            "пользовательскими профилями."
        )
        allure.dynamic.parent_suite("PT Matrix")
        allure.dynamic.suite("Test-drive")
        allure.dynamic.sub_suite(f"{settings.PRODUCT_NAME} {settings.BASIC_CASE_ID}")
        allure.dynamic.epic("PT Matrix")
        allure.dynamic.feature(settings.PRODUCT_NAME)
        allure.dynamic.story(f"Test-drive {settings.BASIC_CASE_ID}")
        allure.dynamic.tag(
            "test-drive",
            settings.PRODUCT_SLUG,
            settings.BASIC_CASE_ID,
        )
        allure.dynamic.parameter(
            "product_name",
            settings.PRODUCT_SLUG,
        )
        allure.dynamic.parameter(
            "case_id",
            settings.BASIC_CASE_ID,
        )
        allure.dynamic.parameter("headless", settings.HEADLESS)
        allure.dynamic.link(
            settings.SCENARIO_URL,
            link_type="scenario",
            name="Открыть test-drive в PT Matrix",
        )
    except Exception as exc:
        _log_reporting_error("метаданных test-drive", exc)


def publish_allure_results(*, collector: ResultCollector) -> None:
    if not _is_available():
        return

    try:
        results = collector.results
        allure.attach(
            collector.render_text_summary(),
            name="Итоговая сводка test-drive",
            attachment_type=allure.attachment_type.TEXT,
        )
        allure.attach(
            _json_text(
                {
                    "scenario": collector.scenario_label,
                    "summary": collector.get_summary(),
                    "checks": [asdict(result) for result in results],
                }
            ),
            name="Все проверки JSON",
            attachment_type=allure.attachment_type.JSON,
        )

        for (username, profile), sections in sorted(
            _group_results(results).items(),
            key=lambda item: (
                item[0][0].casefold(),
                item[0][1].casefold(),
            ),
        ):
            user_results = [
                result
                for steps in sections.values()
                for checks in steps.values()
                for result in checks
            ]
            status = _aggregate_status(user_results)
            message = _aggregate_message(user_results)
            with _allure_step(
                f"Пользователь: {username}",
                status,
                message,
            ):
                with _allure_step(
                    f"Профиль: {profile}",
                    status,
                    message,
                ):
                    _publish_sections(sections)
    except Exception as exc:
        _log_reporting_error("Allure-отчета", exc)


def _publish_sections(
    sections: dict[
        int | None,
        dict[int | None, list[CheckResult]],
    ],
) -> None:
    for section, steps in sorted(
        sections.items(),
        key=lambda item: _optional_number_key(item[0]),
    ):
        section_results = [result for checks in steps.values() for result in checks]
        title = f"Раздел {section}" if section is not None else "Общие проверки"
        with _allure_step(
            title,
            _aggregate_status(section_results),
            _aggregate_message(section_results),
        ):
            for step, checks in sorted(
                steps.items(),
                key=lambda item: _optional_number_key(item[0]),
            ):
                step_title = (
                    f"Модуль / этап {step}" if step is not None else "Вне модуля"
                )
                with _allure_step(
                    step_title,
                    _aggregate_status(checks),
                    _aggregate_message(checks),
                ):
                    for result in checks:
                        _publish_check(result)


def _publish_check(result: CheckResult) -> None:
    icon, status_label = STATUS_LABELS[result.status]
    with _allure_step(
        f"{icon} {status_label} · {result.action}",
        result.status,
        result.message,
    ):
        allure.attach(
            result.message or "Сообщение не указано",
            name="Сообщение",
            attachment_type=allure.attachment_type.TEXT,
        )
        if result.screenshot:
            screenshot_path = Path(result.screenshot)
            if screenshot_path.is_file():
                allure.attach.file(
                    str(screenshot_path),
                    name="Скриншот при FAILED/ERROR",
                    attachment_type=allure.attachment_type.PNG,
                )
            else:
                allure.attach(
                    f"Файл скриншота не найден: {screenshot_path}",
                    name="Предупреждение о скриншоте",
                    attachment_type=allure.attachment_type.TEXT,
                )


@contextmanager
def _allure_step(
    title: str,
    status: CheckStatus,
    message: str,
) -> Iterator[None]:
    step_uuid = str(uuid4())
    plugin_manager.hook.start_step(
        uuid=step_uuid,
        title=title,
        params={},
    )
    try:
        yield
    except BaseException as exc:
        plugin_manager.hook.stop_step(
            uuid=step_uuid,
            title=title,
            exc_type=type(exc),
            exc_val=exc,
            exc_tb=exc.__traceback__,
        )
        raise
    else:
        status_exception = _status_exception(status, message)
        plugin_manager.hook.stop_step(
            uuid=step_uuid,
            title=title,
            exc_type=(type(status_exception) if status_exception else None),
            exc_val=status_exception,
            exc_tb=None,
        )


def _status_exception(
    status: CheckStatus,
    message: str,
) -> BaseException | None:
    if status == CheckStatus.FAILED:
        return AssertionError(message)
    if status == CheckStatus.ERROR:
        return RuntimeError(message)
    if status == CheckStatus.SKIPPED:
        return pytest.skip.Exception(message)
    return None


def _aggregate_status(
    results: list[CheckResult],
) -> CheckStatus:
    statuses = {result.status for result in results}
    if CheckStatus.ERROR in statuses:
        return CheckStatus.ERROR
    if CheckStatus.FAILED in statuses:
        return CheckStatus.FAILED
    if statuses == {CheckStatus.SKIPPED}:
        return CheckStatus.SKIPPED
    return CheckStatus.PASSED


def _aggregate_message(results: list[CheckResult]) -> str:
    counts = {
        status: sum(result.status == status for result in results)
        for status in CheckStatus
    }
    return (
        ", ".join(
            f"{status.value.upper()}: {counts[status]}"
            for status in CheckStatus
            if counts[status]
        )
        or "Проверок нет"
    )


def _group_results(
    results: list[CheckResult],
) -> dict[
    tuple[str, str],
    dict[int | None, dict[int | None, list[CheckResult]]],
]:
    grouped: dict[
        tuple[str, str],
        dict[int | None, dict[int | None, list[CheckResult]]],
    ] = {}
    for result in results:
        user = grouped.setdefault(
            (result.username, result.profile),
            {},
        )
        section = user.setdefault(result.section, {})
        section.setdefault(result.step, []).append(result)
    return grouped


def _optional_number_key(
    value: int | None,
) -> tuple[int, int]:
    return (0, 0) if value is None else (1, value)


def _json_text(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def _is_available() -> bool:
    return allure is not None and plugin_manager is not None


def _log_reporting_error(
    section: str,
    exc: Exception,
) -> None:
    message = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    log_step(f"[ALLURE] Не удалось сформировать часть {section}: {message}")
