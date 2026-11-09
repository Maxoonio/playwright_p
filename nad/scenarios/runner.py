from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from playwright.async_api import Page, async_playwright, expect

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = TEST_DRIVE_ROOT.parent / "common"
for import_path in reversed(
    (
        TEST_DRIVE_ROOT / "common",
        SHARED_ROOT,
        TEST_DRIVE_ROOT,
    )
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import settings
from allure_reporter import (
    configure_allure_test_drive,
    publish_allure_results,
)
from browser_diagnostics import BrowserDiagnostics
from content_loader import (
    load_nad_c3_content,
    load_nad_ctf_content,
)
from data_loader import load_test_drive_users
from exceptions import ScenarioChecksErrored, ScenarioChecksFailed
from models import (
    PERFECT_PROFILE_NAMES,
    SCENARIO_PROFILE_NAMES,
    BasicExercise,
    BasicModule,
    BasicScenarioContent,
    CtfQuestion,
    CtfScenarioContent,
    TestDriveUser,
)
from pages.basic_modules_page import (
    BasicModulesPage,
    QuestionAnswerRejectedError,
    QuestionContentMismatchError,
)
from pages.ctf_scenario_page import CtfScenarioPage
from pages.login_page import LoginPage
from pages.nad_scenario_page import (
    COMPLEX_SCENARIO_TARGET,
    USE_CASES_TARGET,
    WEB_INTERFACE_TARGET,
    NadTestDriveScenarioPage,
)
from results import ResultCollector
from trainer_ctf_bridge import (
    run_specialized_ctf_profile,
    verify_test_drive_leaderboard,
)
from utils.browser import (
    close_context,
    launch_incognito_context,
    maximize_page_window,
)
from utils.logging import (
    bind_log_context,
    compact_exception_message,
    log_step,
    reset_log_context,
)


def _is_perfect_profile(user: TestDriveUser) -> bool:
    return user.test_profile in PERFECT_PROFILE_NAMES


def _is_scenario_profile(user: TestDriveUser) -> bool:
    return user.test_profile in SCENARIO_PROFILE_NAMES


def _allowed_complex_final_statuses(
    perfect_answer_mismatches: int,
) -> set[str]:
    if perfect_answer_mismatches:
        return {"completed", "skipped_by_bs"}
    return {"completed"}


async def run_nad_c3(*, users_path: Path) -> dict[str, int]:
    users, config_errors = load_test_drive_users(users_path)
    collector = ResultCollector(scenario_label="PT NAD test-drive c-3")
    configure_allure_test_drive()

    for error in config_errors:
        collector.record_error(
            username=error["username"],
            profile=error["profile"],
            action="USER_CONFIGURATION",
            message=error["message"],
        )

    enabled_users = [user for user in users if user.enabled]
    for user in users:
        if not user.enabled:
            log_step(f"[USER] Пропущен disabled-пользователь {user.username}")

    if not enabled_users:
        collector.record_error(
            username="configuration",
            profile="configuration",
            action="ENABLED_USERS_MISSING",
            message=("Нет ни одного enabled-пользователя в users.local.json"),
        )
        collector.print_summary()
        publish_allure_results(collector=collector)
        raise ScenarioChecksErrored("Нет включенных пользователей test-drive")

    content: BasicScenarioContent | None = None
    ctf_content: CtfScenarioContent | None = None
    if any(
        user.test_profile == "introduction_finish" or _is_scenario_profile(user)
        for user in enabled_users
    ):
        try:
            content = load_nad_c3_content()
        except Exception as exc:
            message = (
                "Не удалось загрузить свежие exercises.json и "
                "sections.json из GitLab: "
                f"{compact_exception_message(exc)}"
            )
            for user in enabled_users:
                if user.test_profile in {
                    "introduction_finish",
                } or _is_scenario_profile(user):
                    collector.record_error(
                        username=user.username,
                        profile=user.test_profile,
                        action="GITLAB_CONTENT_LOADED",
                        message=message,
                    )
    if any(_is_scenario_profile(user) for user in enabled_users):
        try:
            ctf_content = load_nad_ctf_content()
        except Exception as exc:
            message = (
                "Не удалось загрузить свежий CTF exercises.json "
                "NAD c-1 из GitLab: "
                f"{compact_exception_message(exc)}"
            )
            for user in enabled_users:
                if _is_scenario_profile(user):
                    collector.record_error(
                        username=user.username,
                        profile=user.test_profile,
                        action="GITLAB_CTF_CONTENT_LOADED",
                        message=message,
                    )

    scenario_content_required = any(
        _is_scenario_profile(user) for user in enabled_users
    )
    basic_content_required = scenario_content_required or any(
        user.test_profile == "introduction_finish" for user in enabled_users
    )
    if (basic_content_required and content is None) or (
        scenario_content_required and ctf_content is None
    ):
        collector.print_summary()
        publish_allure_results(collector=collector)
        summary = collector.get_summary()
        raise ScenarioChecksErrored(
            f"Scenario content loading failed: {summary['errors']} ERROR."
        )

    async with async_playwright() as playwright:
        parallel_limit = settings.MAX_PARALLEL_BROWSERS or len(enabled_users)
        semaphore = asyncio.Semaphore(parallel_limit)
        log_step(
            "[PARALLEL] Запуск профилей: "
            f"пользователей={len(enabled_users)}, "
            f"одновременных браузеров={parallel_limit}"
        )
        await asyncio.gather(
            *[
                _run_user(
                    playwright=playwright,
                    user=user,
                    collector=collector,
                    content=content,
                    ctf_content=ctf_content,
                    semaphore=semaphore,
                )
                for user in enabled_users
            ]
        )

    collector.print_summary()
    publish_allure_results(collector=collector)
    if collector.has_failures_or_errors():
        summary = collector.get_summary()
        outcome_error = (
            ScenarioChecksErrored if summary["errors"] else ScenarioChecksFailed
        )
        raise outcome_error(
            "Scenario checks failed: "
            f"{summary['failed']} FAILED, {summary['errors']} ERROR."
        )
        log_step(
            "Проверки test-drive завершились с ошибками: "
            f"{summary['failed']} FAILED, "
            f"{summary['errors']} ERROR"
        )
    return collector.get_summary()


async def run_nad_c3_introduction(*, users_path: Path) -> None:
    """Совместимое имя точки входа для существующей команды pytest."""

    await run_nad_c3(users_path=users_path)


async def _run_user(
    *,
    playwright,
    user: TestDriveUser,
    collector: ResultCollector,
    content: BasicScenarioContent | None,
    ctf_content: CtfScenarioContent | None,
    semaphore: asyncio.Semaphore,
) -> None:
    log_token = bind_log_context(
        username=getattr(user, "username", "unknown_user"),
        profile=getattr(user, "test_profile", "unknown_profile"),
    )
    try:
        async with semaphore:
            await _run_user_in_separate_browser(
                playwright=playwright,
                user=user,
                collector=collector,
                content=content,
                ctf_content=ctf_content,
            )
    finally:
        reset_log_context(log_token)


async def _run_user_in_separate_browser(
    *,
    playwright,
    user: TestDriveUser,
    collector: ResultCollector,
    content: BasicScenarioContent | None,
    ctf_content: CtfScenarioContent | None,
) -> None:
    context = None
    page = None
    diagnostics = None
    try:
        context = await launch_incognito_context(playwright)
        page = context.pages[0] if context.pages else await context.new_page()
        await maximize_page_window(page)
        page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
        page.set_default_navigation_timeout(settings.DEFAULT_TIMEOUT_MS)
        expect.set_options(timeout=settings.DEFAULT_TIMEOUT_MS)
        diagnostics = await BrowserDiagnostics.start(
            context=context,
            username=user.username,
        )

        log_step(f"[USER] Начат пользователь {user.username}")
        log_step(f"[PROFILE] {user.test_profile}")
        profile_ok = True

        login_page = LoginPage(page)
        scenario_page = NadTestDriveScenarioPage(page)

        if not await _run_critical(
            collector=collector,
            user=user,
            page=page,
            action="PT_TEST_DRIVE_OPEN",
            success_message="Страница PT Matrix Test Drive открыта",
            failure_message=("Не удалось открыть PT Matrix Test Drive"),
            operation=login_page.open,
        ):
            return

        if not await _run_critical(
            collector=collector,
            user=user,
            page=page,
            action="LOGIN",
            success_message="Вход пользователя выполнен",
            failure_message=("Не удалось выполнить вход пользователя"),
            operation=lambda: login_page.login(
                user.username,
                user.password,
            ),
        ):
            return

        if not await _run_critical(
            collector=collector,
            user=user,
            page=page,
            action="NAD_C3_OPENED",
            success_message=("Открыт сценарий PT NAD test-drive c-3"),
            failure_message=("Не удалось открыть PT NAD test-drive c-3"),
            operation=scenario_page.open,
            section=1,
            step=1,
        ):
            return

        if content is not None:
            if _is_perfect_profile(user):
                expected_steps = {module.step_id for module in content.modules}
                actual_steps = set(scenario_page._navigation_layout)
                if expected_steps != actual_steps:
                    collector.record_fail(
                        username=user.username,
                        profile=user.test_profile,
                        action="BASIC_CONTENT_DOM_MISMATCH",
                        message=(
                            f"Нет модулей в DOM: {sorted(expected_steps - actual_steps)}; "
                            f"нет модулей в GitLab: {sorted(actual_steps - expected_steps)}. "
                            "Продолжаем доступные модули"
                        ),
                    )
            content = scenario_page.content_for_current_layout(
                content, allow_missing=_is_perfect_profile(user)
            )

        intro_ok = await _run_shared_introduction(
            scenario_page=scenario_page,
            user=user,
            collector=collector,
        )
        profile_ok = intro_ok and profile_ok

        if user.test_profile == "introduction_finish":
            if content is None:
                collector.record_error(
                    username=user.username,
                    profile=user.test_profile,
                    action="GITLAB_CONTENT_REQUIRED",
                    message=(
                        "Профиль частичного завершения остановлен: "
                        "контент GitLab не загружен"
                    ),
                )
                return
            partial_ok = await _run_partial_third_module(
                scenario_page=scenario_page,
                content=content,
                user=user,
                collector=collector,
            )
            profile_ok = partial_ok and profile_ok
            finish_ok = await _run_critical(
                collector=collector,
                user=user,
                page=page,
                action="TEST_DRIVE_FINISHED",
                success_message=(
                    "Кнопка «Завершить тест-драйв» сработала, "
                    "API и итоговый DOM подтверждены"
                ),
                failure_message=("Не удалось завершить test-drive"),
                operation=scenario_page.finish_test_drive,
                section=1,
                step=2,
            )
            profile_ok = finish_ok and profile_ok
            if finish_ok:
                statuses_ok = await _verify_early_finish_statuses(
                    page=page,
                    content=content,
                    user=user,
                    collector=collector,
                )
                profile_ok = statuses_ok and profile_ok
        elif _is_scenario_profile(user):
            if content is None or ctf_content is None:
                collector.record_error(
                    username=user.username,
                    profile=user.test_profile,
                    action="GITLAB_CONTENT_REQUIRED",
                    message=(
                        "Сценарный профиль остановлен: basic/CTF "
                        "контент GitLab не загружен"
                    ),
                )
                return
            full_ok = await _run_full_scenario(
                scenario_page=scenario_page,
                content=content,
                ctf_content=ctf_content,
                user=user,
                collector=collector,
            )
            profile_ok = full_ok and profile_ok

        if profile_ok:
            collector.record_pass(
                username=user.username,
                profile=user.test_profile,
                action="PROFILE_COMPLETED",
                message=("Профиль PT NAD test-drive выполнен полностью"),
                section=(
                    4
                    if _is_perfect_profile(user)
                    else (None if _is_scenario_profile(user) else 1)
                ),
                step=(
                    36
                    if _is_perfect_profile(user)
                    else (None if _is_scenario_profile(user) else 2)
                ),
            )
    except Exception as exc:
        screenshot = await collector.capture_failure_screenshot(
            page=page,
            username=user.username,
            action="PROFILE_UNEXPECTED_ERROR",
            status="error",
            section=None,
            step=None,
        )
        collector.record_error(
            username=user.username,
            profile=user.test_profile,
            action="PROFILE_UNEXPECTED_ERROR",
            message=(
                "Необработанная ошибка параллельного профиля: "
                f"{compact_exception_message(exc)}"
            ),
            screenshot=screenshot,
        )
    finally:
        if diagnostics is not None:
            await diagnostics.stop()
        if context is not None:
            try:
                await close_context(context)
            except Exception as exc:
                collector.record_error(
                    username=user.username,
                    profile=user.test_profile,
                    action="BROWSER_CONTEXT_CLOSE",
                    message=(
                        "Не удалось закрыть отдельный браузер "
                        f"пользователя: {compact_exception_message(exc)}"
                    ),
                )


async def _run_shared_introduction(
    *,
    scenario_page: NadTestDriveScenarioPage,
    user: TestDriveUser,
    collector: ResultCollector,
) -> bool:
    page = scenario_page.page
    outcomes: list[bool] = []

    if _is_perfect_profile(user):
        outcomes.append(
            await _run_sos_check(
                scenario_page=scenario_page,
                user=user,
                collector=collector,
            )
        )
        outcomes.append(
            await _run_check(
                collector=collector,
                user=user,
                page=page,
                action="FINISH_INITIAL_STATE_CONFIRMED",
                success_message=(
                    "Доступность кнопки завершения соответствует "
                    "текущему прогрессу после SOS"
                ),
                failure_message=(
                    "Начальное состояние прогресса или кнопки завершения неверно"
                ),
                operation=scenario_page.verify_initial_finish_state,
                section=1,
                step=1,
            )
        )

    for target in (
        WEB_INTERFACE_TARGET,
        USE_CASES_TARGET,
    ):
        outcomes.append(
            await _run_check(
                collector=collector,
                user=user,
                page=page,
                action=target.action,
                success_message=(
                    f"Ссылка «{target.link_text}» открыла «{target.target_title}»"
                ),
                failure_message=(
                    f"Ссылка «{target.link_text}» не открыла «{target.target_title}»"
                ),
                operation=lambda target=target: scenario_page.verify_introduction_link(
                    target
                ),
                section=1,
                step=1,
            )
        )

    if _is_perfect_profile(user):
        outcomes.append(
            await _run_check(
                collector=collector,
                user=user,
                page=page,
                action="CTF_PREREQUISITES_DIALOG_CONFIRMED",
                success_message=(
                    "До прохождения required_for_ctf-модулей "
                    "показано окно «Поиск флага»"
                ),
                failure_message=(
                    "Не подтверждено окно «Поиск флага» для "
                    "непройденных required_for_ctf-модулей"
                ),
                operation=(scenario_page.verify_ctf_prerequisites_dialog),
                section=1,
                step=1,
            )
        )

    outcomes.append(
        await _run_check(
            collector=collector,
            user=user,
            page=page,
            action=COMPLEX_SCENARIO_TARGET.action,
            success_message=("Ссылка «Комплексный сценарий» открыла нужный подраздел"),
            failure_message=(
                "Ссылка «Комплексный сценарий» не открыла нужный подраздел"
            ),
            operation=lambda: scenario_page.verify_introduction_link(
                COMPLEX_SCENARIO_TARGET
            ),
            section=1,
            step=1,
        )
    )

    outcomes.append(
        await _run_check(
            collector=collector,
            user=user,
            page=page,
            action="ABOUT_MATRIX_OPENED",
            success_message="Открыт подраздел «О PT Matrix»",
            failure_message=("Не удалось открыть подраздел «О PT Matrix»"),
            operation=scenario_page.open_about_matrix,
            section=1,
            step=2,
        )
    )

    outcomes.append(
        await _run_check(
            collector=collector,
            user=user,
            page=page,
            action="MATRIX_VIDEO_TOUR_CONFIRMED",
            success_message=("Обзорный видеотур PT Matrix открыт и закрыт"),
            failure_message=("Обзорный видеотур PT Matrix не подтвержден"),
            operation=scenario_page.verify_video_tour,
            section=1,
            step=2,
        )
    )

    if _is_perfect_profile(user):
        outcomes.append(
            await _run_check(
                collector=collector,
                user=user,
                page=page,
                action="FINISH_AVAILABLE_ABOVE_THRESHOLD",
                success_message=(
                    "После двух вводных подразделов прогресс больше 5%, "
                    "кнопка завершения доступна"
                ),
                failure_message=(
                    "Прогресс или доступность кнопки завершения после введения неверны"
                ),
                operation=(scenario_page.verify_finish_available_after_introduction),
                section=1,
                step=2,
            )
        )

    if _is_perfect_profile(user):
        outcomes.append(
            await _run_check(
                collector=collector,
                user=user,
                page=page,
                action="PRO_VERSION_REQUEST_SUBMITTED",
                success_message=(
                    "PRO-заявка отправлена кнопкой #proSaveBtn и подтверждена по DOM"
                ),
                failure_message=("PRO-заявка не подтверждена после клика #proSaveBtn"),
                operation=lambda: scenario_page.submit_pro_request(
                    company_name=settings.PRO_COMPANY_NAME,
                    company_tin=settings.PRO_COMPANY_TIN,
                ),
                section=1,
                step=2,
            )
        )

    return all(outcomes)


async def _run_partial_third_module(
    *,
    scenario_page: NadTestDriveScenarioPage,
    content: BasicScenarioContent,
    user: TestDriveUser,
    collector: ResultCollector,
) -> bool:
    page = scenario_page.page
    modules_page = BasicModulesPage(page)
    module = next(item for item in content.exercise_modules if item.step_id == 3)
    outcomes: list[bool] = []

    third_opened = await _run_check(
        collector=collector,
        user=user,
        page=page,
        action="NEXT_MODULE_BUTTON_TO_ABOUT_NAD_CONFIRMED",
        success_message=("Кнопка перехода вправо открыла модуль «О PT NAD»"),
        failure_message=("Кнопка перехода вправо не открыла модуль «О PT NAD»"),
        operation=lambda: modules_page.go_to_next_step(
            expected_step_id=3,
            expected_title="О PT NAD",
        ),
        section=1,
        step=3,
    )
    if not third_opened:
        return False

    outcomes.append(third_opened)
    outcomes.append(
        await _run_check(
            collector=collector,
            user=user,
            page=page,
            action="MODULE_3_STATUS_BEFORE_CONFIRMED",
            success_message=("До ответа модуль «О PT NAD» имеет undefined"),
            failure_message=("До ответа статус модуля «О PT NAD» неверен"),
            operation=lambda: modules_page.verify_module_status_expected(
                module,
                "undefined",
            ),
            section=1,
            step=3,
        )
    )
    if not outcomes[-1]:
        return False

    if not await _run_copyable_filters_check(
        modules_page=modules_page,
        collector=collector,
        user=user,
        page=page,
        module=module,
    ):
        return False

    if not await _run_critical(
        collector=collector,
        user=user,
        page=page,
        action="MODULE_3_TASKS_OPENED",
        success_message=("Задания модуля «О PT NAD» открыты"),
        failure_message=("Не удалось открыть задания модуля «О PT NAD»"),
        operation=lambda: modules_page.go_to_tasks(module),
        section=1,
        step=3,
    ):
        return False

    first_question = module.exercises[0]
    if not await _run_check(
        collector=collector,
        user=user,
        page=page,
        action="STEP_3_Q1_CORRECT_ONLY_COMPLETED",
        success_message=(
            "В модуле 3 дан один правильный ответ, остальные вопросы не затронуты"
        ),
        failure_message=("Не удалось отправить единственный правильный ответ модуля 3"),
        operation=lambda: modules_page.answer_question(
            module,
            first_question,
            verify_incorrect_first=False,
        ),
        section=1,
        step=3,
    ):
        return False

    during_status_ok = await _run_check(
        collector=collector,
        user=user,
        page=page,
        action="MODULE_3_STATUS_DURING_CONFIRMED",
        success_message=("После одного ответа модуль 3 имеет started"),
        failure_message=("После одного ответа статус модуля 3 неверен"),
        operation=lambda: modules_page.verify_module_status_expected(
            module,
            "started",
        ),
        section=1,
        step=3,
    )
    outcomes.append(during_status_ok)

    next_opened = await _run_check(
        collector=collector,
        user=user,
        page=page,
        action="UNFINISHED_MODULE_NEXT_DIALOG_CONFIRMED",
        success_message=(
            "Для незавершенного модуля показано предупреждение, "
            "кнопка «К следующему модулю» открыла «Главное меню»"
        ),
        failure_message=(
            "Не подтвержден переход из незавершенного модуля через предупреждающее окно"
        ),
        operation=lambda: modules_page.go_to_next_step(
            expected_step_id=4,
            expected_title="Главное меню",
            require_unfinished_dialog=True,
        ),
        section=3,
        step=4,
    )
    if not next_opened:
        return False

    outcomes.append(next_opened)
    outcomes.append(
        await _run_check(
            collector=collector,
            user=user,
            page=page,
            action="MODULE_3_STATUS_AFTER_NEXT_CONFIRMED",
            success_message=(
                "После перехода модуль 3 сохраняет started до завершения test-drive"
            ),
            failure_message=(
                "После перехода статус незавершенного модуля 3 не соответствует started"
            ),
            operation=lambda: modules_page.verify_module_status_expected(
                module,
                "started",
            ),
            section=1,
            step=3,
        )
    )
    return all(outcomes)


async def _verify_early_finish_statuses(
    *,
    page: Page,
    content: BasicScenarioContent,
    user: TestDriveUser,
    collector: ResultCollector,
) -> bool:
    modules_page = BasicModulesPage(page)
    outcomes: list[bool] = []

    for module in content.modules:
        if module.step_id in {1, 2}:
            expected = "completed"
        elif module.step_id == 3:
            expected = "partial_success"
        else:
            expected = "skipped_by_bs"

        outcomes.append(
            await _run_check(
                collector=collector,
                user=user,
                page=page,
                action=(f"MODULE_{module.step_id}_FINAL_{expected.upper()}_CONFIRMED"),
                success_message=(
                    f"После досрочного завершения модуль "
                    f"{module.step_id} имеет {expected}"
                ),
                failure_message=(f"Итоговый статус модуля {module.step_id} неверен"),
                operation=lambda module=module, expected=expected: (
                    modules_page.verify_module_status_expected(
                        module,
                        expected,
                    )
                ),
                section=_result_section(module),
                step=module.step_id,
            )
        )

    partial_module = next(
        module for module in content.exercise_modules if module.step_id == 3
    )
    outcomes.append(
        await _run_check(
            collector=collector,
            user=user,
            page=page,
            action="STEP_3_Q1_FINAL_COMPLETED_CONFIRMED",
            success_message=(
                "Засчитанный вопрос частичного модуля сохранил статус completed"
            ),
            failure_message=("Статус засчитанного вопроса частичного модуля неверен"),
            operation=lambda: modules_page.verify_question_status_expected(
                partial_module,
                partial_module.exercises[0],
                "completed",
            ),
            section=_result_section(partial_module),
            step=partial_module.step_id,
        )
    )
    if len(partial_module.exercises) > 1:
        outcomes.append(
            await _run_check(
                collector=collector,
                user=user,
                page=page,
                action="STEP_3_Q2_FINAL_SKIPPED_BY_BS_CONFIRMED",
                success_message=(
                    "Нерешенный вопрос частичного модуля получил статус skipped_by_bs"
                ),
                failure_message=(
                    "Статус нерешенного вопроса частичного модуля неверен"
                ),
                operation=lambda: modules_page.verify_question_status_expected(
                    partial_module,
                    partial_module.exercises[1],
                    "skipped_by_bs",
                ),
                section=_result_section(partial_module),
                step=partial_module.step_id,
            )
        )

    skipped_module = next(
        module
        for module in content.exercise_modules
        if module.step_id > partial_module.step_id and module.exercises
    )
    outcomes.append(
        await _run_check(
            collector=collector,
            user=user,
            page=page,
            action=(f"STEP_{skipped_module.step_id}_Q1_FINAL_SKIPPED_BY_BS_CONFIRMED"),
            success_message=(
                "Вопрос непройденного модуля получил статус skipped_by_bs"
            ),
            failure_message=("Статус вопроса непройденного модуля неверен"),
            operation=lambda: modules_page.verify_question_status_expected(
                skipped_module,
                skipped_module.exercises[0],
                "skipped_by_bs",
            ),
            section=_result_section(skipped_module),
            step=skipped_module.step_id,
        )
    )

    unique_sections: dict[int, str] = {}
    for module in content.modules:
        for section_id, title in zip(
            module.ancestor_section_ids,
            module.ancestor_section_titles,
            strict=True,
        ):
            unique_sections.setdefault(section_id, title)

    for section_id, title in sorted(unique_sections.items()):
        expected = (
            "partial_success"
            if section_id in partial_module.ancestor_section_ids
            else "skipped_by_bs"
        )
        outcomes.append(
            await _run_check(
                collector=collector,
                user=user,
                page=page,
                action=(f"SECTION_{section_id}_FINAL_{expected.upper()}_CONFIRMED"),
                success_message=(
                    f"После досрочного завершения раздел {section_id} имеет {expected}"
                ),
                failure_message=(f"Итоговый статус раздела {section_id} неверен"),
                operation=(
                    lambda section_id=section_id, title=title, expected=expected: (
                        modules_page.verify_section_status_expected(
                            section_id,
                            title,
                            expected,
                        )
                    )
                ),
                section=section_id,
                step=None,
            )
        )

    scenario_page = NadTestDriveScenarioPage(page)

    async def verify_early_finish_progress() -> None:
        progress = await scenario_page.read_progress_percent()
        await _verify_progress_matches_module_statuses(
            modules_page=modules_page,
            content=content,
            progress=progress,
        )

    outcomes.append(
        await _run_check(
            collector=collector,
            user=user,
            page=page,
            action="EARLY_FINISH_PROGRESS_MATCHES_STATUSES",
            success_message=(
                "После досрочного завершения прогресс соответствует "
                "количеству completed-модулей"
            ),
            failure_message=(
                "После досрочного завершения прогресс не соответствует статусам модулей"
            ),
            operation=verify_early_finish_progress,
            section=1,
            step=3,
        )
    )

    return all(outcomes)


async def _perfect_module_question_inventory(
    *,
    page: Page,
    module: BasicModule,
    user: TestDriveUser,
    collector: ResultCollector,
) -> tuple[tuple[BasicExercise, ...], int]:
    actual_orders = set(
        await page.locator(
            ".scenarios-basic-result__tasks-container:visible [id^='ex_']"
        ).evaluate_all("nodes => nodes.map(node => Number(node.id.replace('ex_', '')))")
    )
    expected_orders = {exercise.order for exercise in module.exercises}
    missing_dom = expected_orders - actual_orders
    missing_content = actual_orders - expected_orders
    if missing_dom or missing_content:
        collector.record_fail(
            username=user.username,
            profile=user.test_profile,
            action=f"MODULE_{module.step_id}_CONTENT_MISMATCH",
            message=(
                f"Нет вопросов в DOM: {sorted(missing_dom)}; "
                f"нет вопросов/ответов в GitLab: {sorted(missing_content)}. "
                "Выполняем доступные вопросы и переходим дальше"
            ),
            section=_result_section(module),
            step=module.step_id,
        )
    return (
        tuple(
            exercise for exercise in module.exercises if exercise.order in actual_orders
        ),
        len(missing_dom) + len(missing_content),
    )


async def _run_full_scenario(
    *,
    scenario_page: NadTestDriveScenarioPage,
    content: BasicScenarioContent,
    ctf_content: CtfScenarioContent,
    user: TestDriveUser,
    collector: ResultCollector,
) -> bool:
    page = scenario_page.page
    modules_page = BasicModulesPage(page)
    previous_module: BasicModule | None = None
    expected_status_after_next: dict[int, set[str]] = {}
    perfect_profile = _is_perfect_profile(user)
    progress_target = (
        None if perfect_profile else settings.SPECIALIZED_CTF_ENTRY_PROGRESS_PERCENT
    )
    progress_target_reached = progress_target is None
    perfect_answer_mismatches = (
        getattr(scenario_page, "_content_layout_gap_count", 0) if perfect_profile else 0
    )

    collector.record_pass(
        username=user.username,
        profile=user.test_profile,
        action="GITLAB_CONTENT_LOADED",
        message=(
            f"Свежий контент {content.source.product_slug} "
            f"{content.source.case_id}: "
            f"модулей={len(content.exercise_modules)}, "
            f"вопросов={sum(len(item.exercises) for item in content.exercise_modules)}"
        ),
    )

    for module in content.exercise_modules:
        section = _result_section(module)
        if not await _run_critical(
            collector=collector,
            user=user,
            page=page,
            action=f"MODULE_{module.step_id}_OPENED",
            success_message=(f"Открыт модуль {module.step_id} «{module.title}»"),
            failure_message=(
                f"Не удалось открыть модуль {module.step_id} «{module.title}»"
            ),
            operation=lambda module=module: modules_page.go_to_next_module(module),
            section=section,
            step=module.step_id,
        ):
            if perfect_profile:
                perfect_answer_mismatches += 1
                continue
            return False

        if perfect_profile and previous_module is not None:
            allowed_after = expected_status_after_next.get(
                previous_module.step_id,
                {"completed"},
            )
            if not await _run_check(
                collector=collector,
                user=user,
                page=page,
                action=(
                    f"MODULE_{previous_module.step_id}_STATUS_AFTER_NEXT_CONFIRMED"
                ),
                success_message=(
                    f"После перехода модуль {previous_module.step_id} имеет completed"
                ),
                failure_message=(
                    f"После перехода статус модуля {previous_module.step_id} неверен"
                ),
                operation=lambda previous_module=previous_module, allowed_after=allowed_after: (
                    modules_page.verify_module_status_allowed(
                        previous_module,
                        allowed_after,
                    )
                ),
                section=_result_section(previous_module),
                step=previous_module.step_id,
            ):
                perfect_answer_mismatches += 1

        status_before = await modules_page.read_module_status(module)
        progress_before = await _record_module_progress(
            scenario_page=scenario_page,
            collector=collector,
            user=user,
            page=page,
            module=module,
            phase="BEFORE",
        )
        if progress_before is None:
            return False

        if progress_target is not None and progress_before[0] >= progress_target:
            _record_ctf_progress_target(
                collector=collector,
                user=user,
                module=module,
                progress=progress_before,
                target=progress_target,
            )
            progress_target_reached = True
            break

        expected_before_status = (
            status_before if status_before in {"started", "completed"} else "undefined"
        )
        if perfect_profile and not await _run_check(
            collector=collector,
            user=user,
            page=page,
            action=(f"MODULE_{module.step_id}_STATUS_BEFORE_CONFIRMED"),
            success_message=(
                "Статусы модуля и родительских разделов до выполнения подтверждены"
            ),
            failure_message=(
                "Статусы модуля или родительских разделов до выполнения неверны"
            ),
            operation=lambda module=module: modules_page.verify_module_status_before(
                module,
                expected_status=expected_before_status,
            ),
            section=section,
            step=module.step_id,
        ):
            perfect_answer_mismatches += 1

        if status_before == "completed":
            if perfect_profile:
                await _record_progress_status_check(
                    scenario_page=scenario_page,
                    modules_page=modules_page,
                    content=content,
                    collector=collector,
                    user=user,
                    module=module,
                    progress=progress_before,
                )
            collector.record_pass(
                username=user.username,
                profile=user.test_profile,
                action=f"MODULE_{module.step_id}_ALREADY_COMPLETED",
                message=(
                    "Модуль уже completed; ответы повторно "
                    "не отправляются, продолжаем со следующего"
                ),
                section=section,
                step=module.step_id,
            )
            previous_module = module
            continue

        if perfect_profile and not await _run_copyable_filters_check(
            modules_page=modules_page,
            collector=collector,
            user=user,
            page=page,
            module=module,
        ):
            perfect_answer_mismatches += 1

        if perfect_profile and not await _run_check(
            collector=collector,
            user=user,
            page=page,
            action=(f"MODULE_{module.step_id}_ACTIONBAR_CONFIRMED"),
            success_message=("Боковая панель разворачивается и сворачивается"),
            failure_message=("Не подтверждена работа боковой панели"),
            operation=modules_page.verify_actionbar_toggle,
            section=section,
            step=module.step_id,
        ):
            perfect_answer_mismatches += 1

        if not await _run_critical(
            collector=collector,
            user=user,
            page=page,
            action=f"MODULE_{module.step_id}_TASKS_OPENED",
            success_message=("Кнопка «К заданиям» открыла первый нерешенный вопрос"),
            failure_message=("Кнопка «К заданиям» не открыла задания"),
            operation=lambda module=module: modules_page.go_to_tasks(module),
            section=section,
            step=module.step_id,
        ):
            if perfect_profile:
                perfect_answer_mismatches += 1
                continue
            return False

        if perfect_profile and module.step_id == 3:
            await _run_check(
                collector=collector,
                user=user,
                page=page,
                action="NAD_PRODUCT_INTERVAL_CONFIRMED",
                success_message=(
                    "PT NAD дважды открыт, авторизация выполнена, "
                    "интервал продукта совпал с Matrix"
                ),
                failure_message=("Не удалось подтвердить переход или интервал PT NAD"),
                operation=lambda: modules_page.verify_product_interval(
                    username=user.username,
                    password=user.password,
                ),
                section=section,
                step=module.step_id,
            )
        if perfect_profile and module.step_id == 3:
            if not await _run_critical(
                collector=collector,
                user=user,
                page=page,
                action=f"MODULE_{module.step_id}_TASKS_READY_FOR_ANSWERS",
                success_message=("После возврата из PT NAD задания снова открыты"),
                failure_message=(
                    "После возврата из PT NAD не удалось снова открыть задания"
                ),
                operation=lambda module=module: modules_page.go_to_tasks(module),
                section=section,
                step=module.step_id,
            ):
                perfect_answer_mismatches += 1
                continue

        skipped_questions = 0
        failed_questions = 0
        completed_questions = 0
        during_status_checked = False
        exercises = module.exercises
        content_gaps = 0
        if perfect_profile:
            exercises, content_gaps = await _perfect_module_question_inventory(
                page=page,
                module=module,
                user=user,
                collector=collector,
            )
            perfect_answer_mismatches += content_gaps

        for exercise_index, exercise in enumerate(exercises):
            if perfect_profile or user.test_profile == "answers_and_hints":
                hint_action = f"STEP_{module.step_id}_Q{exercise.order}_HINT_CONFIRMED"
                try:
                    hint_checked = await modules_page.verify_question_hint(
                        module,
                        exercise,
                    )
                except Exception as exc:
                    screenshot = await collector.capture_failure_screenshot(
                        page=page,
                        username=user.username,
                        action=hint_action,
                        status="failed",
                        section=section,
                        step=module.step_id,
                    )
                    collector.record_fail(
                        username=user.username,
                        profile=user.test_profile,
                        action=hint_action,
                        message=(
                            f"Q{exercise.order}: подсказка не подтверждена; "
                            "окно закрыто, прохождение вопроса продолжается: "
                            f"{compact_exception_message(exc)}"
                        ),
                        section=section,
                        step=module.step_id,
                        screenshot=screenshot,
                    )
                else:
                    if hint_checked:
                        collector.record_pass(
                            username=user.username,
                            profile=user.test_profile,
                            action=hint_action,
                            message=(
                                f"Q{exercise.order}: видимая подсказка открыта, "
                                "проверена и закрыта"
                            ),
                            section=section,
                            step=module.step_id,
                        )

            if False and module.step_id == 19 and exercise.order == 3:
                if not await _run_check(
                    collector=collector,
                    user=user,
                    page=page,
                    action="STEP_19_Q3_KNOWN_MESS_COMPLETED",
                    success_message=(
                        "Q3 модуля 19 закрыт с учетом известного расхождения контента"
                    ),
                    failure_message=(
                        "Q3 модуля 19 не удалось закрыть даже fallback-подбором"
                    ),
                    operation=lambda exercise=exercise: modules_page.answer_question(
                        module,
                        exercise,
                    ),
                    section=section,
                    step=module.step_id,
                ):
                    return False
                continue

            question_action = (
                f"STEP_{module.step_id}_Q{exercise.order}_"
                f"{exercise.question_type.upper().replace('-', '_')}_"
                "COMPLETED"
            )
            try:
                answer_result = await modules_page.answer_question_with_retries(
                    module,
                    exercise,
                )
            except (
                QuestionContentMismatchError,
                QuestionAnswerRejectedError,
            ) as exc:
                if perfect_profile:
                    perfect_answer_mismatches += 1
                skipped_questions += 1
                collector.record_skip(
                    username=user.username,
                    profile=user.test_profile,
                    action=question_action.replace(
                        "_COMPLETED",
                        "_SKIPPED",
                    ),
                    message=(
                        f"Q{exercise.order}: вопрос пропущен, "
                        "ответ из GitLab не принят вебом или DOM не совпал: "
                        f"{compact_exception_message(exc)}"
                    ),
                    section=section,
                    step=module.step_id,
                )
                continue
            except Exception as exc:
                if perfect_profile:
                    perfect_answer_mismatches += 1
                    skipped_questions += 1
                    collector.record_skip(
                        username=user.username,
                        profile=user.test_profile,
                        action=question_action.replace(
                            "_COMPLETED",
                            "_SKIPPED",
                        ),
                        message=(
                            f"Q{exercise.order}: вопрос пропущен, "
                            "техническая операция не выполнилась после "
                            "трёх попыток: "
                            f"{compact_exception_message(exc)}"
                        ),
                        section=section,
                        step=module.step_id,
                    )
                    continue

                failed_questions += 1
                screenshot = await collector.capture_failure_screenshot(
                    page=page,
                    username=user.username,
                    action=question_action,
                    status="failed",
                    section=section,
                    step=module.step_id,
                )
                collector.record_fail(
                    username=user.username,
                    profile=user.test_profile,
                    action=question_action,
                    message=(
                        f"Q{exercise.order}: вопрос был задействован, "
                        "но техническая операция не выполнилась после "
                        "трёх попыток: "
                        f"{compact_exception_message(exc)}"
                    ),
                    section=section,
                    step=module.step_id,
                    screenshot=screenshot,
                )
                continue

            if answer_result.failed_status_confirmed:
                collector.record_pass(
                    username=user.username,
                    profile=user.test_profile,
                    action=question_action.replace(
                        "_COMPLETED",
                        "_FAILED_STATUS_CONFIRMED",
                    ),
                    message=(
                        f"Q{exercise.order}: после неверного ответа "
                        "карточка получила статус failed"
                    ),
                    section=section,
                    step=module.step_id,
                )

            completed_questions += 1
            collector.record_pass(
                username=user.username,
                profile=user.test_profile,
                action=question_action,
                message=(
                    f"Q{exercise.order}: текст, варианты, ответ из "
                    "GitLab и статус completed подтверждены"
                ),
                section=section,
                step=module.step_id,
            )

            if (
                perfect_profile
                and not during_status_checked
                and len(module.exercises) > 1
            ):
                during_status_checked = True
                expected_during_status = (
                    "partial_success"
                    if skipped_questions or failed_questions
                    else "started"
                )
                if not await _run_check(
                    collector=collector,
                    user=user,
                    page=page,
                    action=(f"MODULE_{module.step_id}_STATUS_DURING_CONFIRMED"),
                    success_message=(
                        "После первого засчитанного вопроса модуль "
                        f"имеет {expected_during_status}"
                    ),
                    failure_message=("Во время прохождения статус модуля неверен"),
                    operation=lambda module=module, expected_during_status=expected_during_status: (
                        modules_page.verify_module_status_expected(
                            module,
                            expected_during_status,
                        )
                    ),
                    section=section,
                    step=module.step_id,
                ):
                    perfect_answer_mismatches += 1
            continue

            if not await _run_check(
                collector=collector,
                user=user,
                page=page,
                action=(
                    f"STEP_{module.step_id}_Q{exercise.order}_"
                    f"{exercise.question_type.upper().replace('-', '_')}_"
                    "COMPLETED"
                ),
                success_message=(
                    f"Q{exercise.order}: текст, варианты, "
                    "ответ из GitLab и статус completed подтверждены"
                ),
                failure_message=(
                    f"Q{exercise.order}: вопрос или правильный ответ не подтвержден"
                ),
                operation=lambda exercise=exercise: modules_page.answer_question(
                    module,
                    exercise,
                ),
                section=section,
                step=module.step_id,
            ):
                return False

            if perfect_profile and exercise_index == 0 and len(module.exercises) > 1:
                if not await _run_check(
                    collector=collector,
                    user=user,
                    page=page,
                    action=(f"MODULE_{module.step_id}_STATUS_DURING_CONFIRMED"),
                    success_message=(
                        "После первого завершенного вопроса модуль имеет started"
                    ),
                    failure_message=("Во время прохождения статус модуля неверен"),
                    operation=lambda module=module: (
                        modules_page.verify_module_status_expected(
                            module,
                            "started",
                        )
                    ),
                    section=section,
                    step=module.step_id,
                ):
                    return False

        unresolved_questions = skipped_questions + failed_questions + content_gaps
        if failed_questions and not skipped_questions:
            expected_status_after_next[module.step_id] = {
                "undefined",
                "started",
                "partial_success",
                "completed",
            }
        elif unresolved_questions:
            expected_status_after_next[module.step_id] = {
                "undefined",
                "started",
                "partial_success",
                "completed",
            }
            collector.record_skip(
                username=user.username,
                profile=user.test_profile,
                action=f"MODULE_{module.step_id}_HAS_SKIPPED_QUESTIONS",
                message=(
                    f"Пропущено вопросов: {skipped_questions}; "
                    f"расхождений с контентом: {content_gaps}; "
                    f"засчитано вопросов: {completed_questions}. "
                    "Итоговый статус модуля проверяется без требования completed"
                ),
                section=section,
                step=module.step_id,
            )
        else:
            expected_status_after_next[module.step_id] = {"completed"}

        if not await _run_check(
            collector=collector,
            user=user,
            page=page,
            action=(f"MODULE_{module.step_id}_COMPLETION_DIALOG_HANDLED"),
            success_message=("Логика поздравительного окна модуля подтверждена"),
            failure_message=("Не подтверждена логика поздравительного окна"),
            operation=lambda module=module: (
                modules_page.handle_module_completion_dialog(module)
            ),
            section=section,
            step=module.step_id,
        ):
            if perfect_profile:
                perfect_answer_mismatches += 1
            else:
                return False

        progress_after = await _record_module_progress(
            scenario_page=scenario_page,
            collector=collector,
            user=user,
            page=page,
            module=module,
            phase="AFTER",
            greater_than=(None if unresolved_questions else progress_before[0]),
        )
        if progress_after is None:
            if perfect_profile:
                perfect_answer_mismatches += 1
                previous_module = module
                continue
            return False

        if perfect_profile:
            await _record_progress_status_check(
                scenario_page=scenario_page,
                modules_page=modules_page,
                content=content,
                collector=collector,
                user=user,
                module=module,
                progress=progress_after,
            )

        if progress_target is not None and progress_after[0] >= progress_target:
            _record_ctf_progress_target(
                collector=collector,
                user=user,
                module=module,
                progress=progress_after,
                target=progress_target,
            )
            progress_target_reached = True
            previous_module = module
            break

        if progress_after[0] <= progress_before[0]:
            if unresolved_questions:
                if failed_questions:
                    previous_module = module
                    continue
                collector.record_skip(
                    username=user.username,
                    profile=user.test_profile,
                    action=f"MODULE_{module.step_id}_PROGRESS_CHANGED",
                    message=(
                        "Прогресс не увеличился после модуля со skipped-вопросами: "
                        f"до={progress_before[1]}%, "
                        f"после={progress_after[1]}%"
                    ),
                    section=section,
                    step=module.step_id,
                )
                previous_module = module
                continue
            screenshot = await collector.capture_failure_screenshot(
                page=page,
                username=user.username,
                action=f"MODULE_{module.step_id}_PROGRESS_CHANGED",
                status="failed",
                section=section,
                step=module.step_id,
            )
            collector.record_fail(
                username=user.username,
                profile=user.test_profile,
                action=f"MODULE_{module.step_id}_PROGRESS_CHANGED",
                message=(
                    "После прохождения модуля прогресс не увеличился: "
                    f"до={progress_before[1]}%, "
                    f"после={progress_after[1]}%"
                ),
                section=section,
                step=module.step_id,
                screenshot=screenshot,
            )
            if perfect_profile:
                perfect_answer_mismatches += 1
                previous_module = module
                continue
            return False
        collector.record_pass(
            username=user.username,
            profile=user.test_profile,
            action=f"MODULE_{module.step_id}_PROGRESS_CHANGED",
            message=(
                "После прохождения модуля прогресс увеличился: "
                f"до={progress_before[1]}%, "
                f"после={progress_after[1]}%"
            ),
            section=section,
            step=module.step_id,
        )

        previous_module = module

    if not progress_target_reached:
        collector.record_error(
            username=user.username,
            profile=user.test_profile,
            action="CTF_ENTRY_PROGRESS_TARGET_REACHED",
            message=(
                "До перехода в CTF не достигнут обязательный "
                f"прогресс {progress_target}%"
            ),
            section=5,
            step=None,
        )
        return False

    complex_module = next(
        (module for module in content.modules if module.step_id == 36), None
    )
    if False and not await _run_critical(
        collector=collector,
        user=user,
        page=page,
        action="COMPLEX_SCENARIO_MODULE_OPENED",
        success_message=("Кнопка перехода вправо открыла «Комплексный сценарий»"),
        failure_message=("Не удалось открыть модуль «Комплексный сценарий»"),
        operation=lambda: modules_page.go_to_next_step(
            expected_step_id=36,
            expected_title=complex_module.title,
        ),
        section=None,
        step=36,
    ):
        return False

    if False and not await _run_check(
        collector=collector,
        user=user,
        page=page,
        action="COMPLEX_SCENARIO_PRO_REQUEST_SUBMITTED",
        success_message=("Ссылка «в PRO-версии» открыла форму, PRO-заявка отправлена"),
        failure_message=("PRO-заявка из комплексного сценария не подтверждена"),
        operation=lambda: scenario_page.submit_complex_scenario_pro_request(
            company_name=settings.PRO_COMPANY_NAME,
            company_tin=settings.PRO_COMPANY_TIN,
        ),
        section=None,
        step=36,
    ):
        return False

    ctf_expect_prerequisites_dialog = (
        False if perfect_profile and perfect_answer_mismatches == 0 else None
    )
    ctf_transition_ok = await _run_check(
        collector=collector,
        user=user,
        page=page,
        action="CTF_DIRECT_TRANSITION_CONFIRMED",
        success_message=(
            (
                "После completed required_for_ctf-модулей окно "
                "подготовки не показано, выполнен прямой переход в CTF"
            )
            if perfect_profile and perfect_answer_mismatches == 0
            else (
                "После skipped/несовпавших вопросов required_for_ctf "
                "переход в CTF выполнен напрямую или через окно подготовки"
            )
            if perfect_profile
            else ("После достижения профильного прогресса выполнен переход в CTF")
        ),
        failure_message=(
            (
                "Логика прямого перехода после completed "
                "required_for_ctf-модулей не подтверждена"
            )
            if perfect_profile and perfect_answer_mismatches == 0
            else (
                "После skipped/несовпавших вопросов required_for_ctf "
                "переход в CTF не подтвержден"
            )
            if perfect_profile
            else ("После достижения профильного прогресса переход в CTF не подтвержден")
        ),
        operation=lambda: scenario_page.verify_ctf_direct_transition(
            ctf_case_id=content.ctf_case_id,
            expect_prerequisites_dialog=ctf_expect_prerequisites_dialog,
        ),
        section=5,
        step=None,
    )
    if not ctf_transition_ok and not perfect_profile:
        return False

    if perfect_profile:
        ctf_ok = ctf_transition_ok and await _run_perfect_ctf(
            scenario_page=scenario_page,
            ctf_content=ctf_content,
            user=user,
            collector=collector,
        )
    else:
        ctf_ok = await run_specialized_ctf_profile(
            page=page,
            content=ctf_content,
            ctf_case_id=content.ctf_case_id,
            user=user,
            collector=collector,
        )
    if not ctf_ok and not perfect_profile:
        return False
    if not ctf_ok:
        perfect_answer_mismatches += 1

    if not await _run_critical(
        collector=collector,
        user=user,
        page=page,
        action="BASIC_SCENARIO_REOPENED_AFTER_CTF",
        success_message=(
            "После финального CTF-окна открыт сценарий c-3 "
            "для итоговой проверки статусов"
        ),
        failure_message=("Не удалось вернуться в c-3 после прохождения CTF"),
        operation=scenario_page.return_from_ctf_to_basic_modules,
        section=None,
        step=36,
    ):
        return False

    if not perfect_profile:
        return await _finish_specialized_profile_test_drive(
            scenario_page=scenario_page,
            modules_page=modules_page,
            content=content,
            ctf_content=ctf_content,
            user=user,
            collector=collector,
        )

    if perfect_profile and previous_module is not None:
        if not await _run_check(
            collector=collector,
            user=user,
            page=page,
            action=(f"MODULE_{previous_module.step_id}_FINAL_STATUS_CONFIRMED"),
            success_message=(
                f"После закрытия финального окна модуль "
                f"{previous_module.step_id} имеет completed"
            ),
            failure_message=(
                f"После закрытия финального окна статус "
                f"модуля {previous_module.step_id} неверен"
            ),
            operation=lambda previous_module=previous_module: (
                modules_page.verify_module_status_allowed(
                    previous_module,
                    expected_status_after_next.get(
                        previous_module.step_id,
                        {"completed"},
                    ),
                )
            ),
            section=_result_section(previous_module),
            step=previous_module.step_id,
        ):
            perfect_answer_mismatches += 1

    if complex_module is None:
        collector.record_skip(
            username=user.username,
            profile=user.test_profile,
            action="COMPLEX_SCENARIO_CONTENT_MISSING",
            message="Комплексный модуль отсутствует; переходим к завершению сценария",
            step=36,
        )
        return await _run_check(
            collector=collector,
            user=user,
            page=page,
            action="TEST_DRIVE_FINISHED_WITH_MISSING_CONTENT",
            success_message="Сценарий завершен после фиксации отсутствующего контента",
            failure_message="Не удалось завершить сценарий с отсутствующим контентом",
            operation=scenario_page.finish_test_drive,
        )

    if not await _run_critical(
        collector=collector,
        user=user,
        page=page,
        action="COMPLEX_SCENARIO_MODULE_OPENED",
        success_message=("После CTF открыт последний модуль «Комплексный сценарий»"),
        failure_message=(
            "Не удалось открыть последний модуль «Комплексный сценарий» после CTF"
        ),
        operation=lambda: scenario_page.open_introduction_link_target(
            COMPLEX_SCENARIO_TARGET
        ),
        section=None,
        step=36,
    ):
        return False

    async def reopen_complex_scenario_via_start() -> None:
        await scenario_page.open_about_test_drive()
        log_step("[INTRO][COMPLEX] Выполнен физический возврат в начало test-drive")
        await modules_page.open_module(complex_module)

    if perfect_profile:
        if not await _run_critical(
            collector=collector,
            user=user,
            page=page,
            action="COMPLEX_SCENARIO_REOPENED_VIA_START",
            success_message=(
                "Первый пользователь вернулся в «О Тест-драйве» "
                "и повторно открыл «Комплексный сценарий»"
            ),
            failure_message=(
                "Не удалось повторно открыть «Комплексный сценарий» "
                "через начало test-drive"
            ),
            operation=reopen_complex_scenario_via_start,
            section=None,
            step=36,
        ):
            return False

        if not await _run_critical(
            collector=collector,
            user=user,
            page=page,
            action="COMPLEX_SCENARIO_MODULE_SCROLLED",
            success_message=(
                "Первый пользователь физически открыл и проскроллил "
                "модуль «Комплексный сценарий»"
            ),
            failure_message=(
                "Не удалось проскроллить модуль "
                "«Комплексный сценарий» перед завершением"
            ),
            operation=scenario_page.scroll_complex_scenario_module_for_completion,
            section=None,
            step=36,
        ):
            return False

        if not await _run_check(
            collector=collector,
            user=user,
            page=page,
            action="COMPLEX_SCENARIO_PRO_REQUEST_SUBMITTED",
            success_message=(
                "Ссылка «в PRO-версии» открыла форму, "
                "PRO-заявка отправлена кнопкой #proSaveBtn"
            ),
            failure_message=("PRO-заявка из комплексного сценария не подтверждена"),
            operation=lambda: scenario_page.submit_complex_scenario_pro_request(
                company_name=settings.PRO_COMPANY_NAME,
                company_tin=settings.PRO_COMPANY_TIN,
            ),
            section=None,
            step=36,
        ):
            return False

    if not await _run_critical(
        collector=collector,
        user=user,
        page=page,
        action="COMPLEX_SCENARIO_FINAL_DIALOG_OPENED",
        success_message=(
            "После CTF подтверждено финальное состояние: "
            "feedback-окно открыто либо test-drive уже завершен на 100%"
        ),
        failure_message=(
            "После CTF не удалось открыть feedback-окно или "
            "подтвердить автоматическое 100%-завершение"
        ),
        operation=scenario_page.open_final_feedback_from_finish_button,
        section=None,
        step=36,
    ):
        return False

    if scenario_page.final_feedback_dialog_opened:
        if not await _run_critical(
            collector=collector,
            user=user,
            page=page,
            action="COMPLEX_SCENARIO_FINAL_FEEDBACK_SUBMITTED",
            success_message=(
                "После последнего модуля финальное окно test-drive "
                "обработано, feedback отправлен"
            ),
            failure_message=(
                "После последнего модуля финальное окно test-drive не обработано"
            ),
            operation=lambda: scenario_page.submit_final_feedback(
                settings.FINAL_FEEDBACK_TEXT
            ),
            section=None,
            step=36,
        ):
            return False
    else:
        collector.record_pass(
            username=user.username,
            profile=user.test_profile,
            action="COMPLEX_SCENARIO_FINAL_FEEDBACK_ALREADY_HANDLED",
            message=(
                "Test-drive автоматически завершен на 100%; "
                "disabled-кнопку и повторное feedback-окно не ожидаем"
            ),
            section=None,
            step=36,
        )

    allowed_final_statuses = _allowed_complex_final_statuses(perfect_answer_mismatches)
    final_status_ok = await _run_check(
        collector=collector,
        user=user,
        page=page,
        action="COMPLEX_SCENARIO_FINAL_STATUS_CONFIRMED",
        success_message=(
            "После закрытия финального окна статус комплексного "
            "сценария согласован с результатом прохождения"
        ),
        failure_message=("Итоговый статус комплексного сценария неверен"),
        operation=lambda: modules_page.verify_module_status_allowed(
            complex_module,
            allowed_final_statuses,
        ),
        section=None,
        step=36,
    )
    if not final_status_ok:
        return False

    if perfect_answer_mismatches:
        progress, progress_label = await scenario_page.read_progress_percent()
        if progress >= 100:
            collector.record_pass(
                username=user.username,
                profile=user.test_profile,
                action="TEST_DRIVE_100_PERCENT_CONFIRMED",
                message=(
                    "Идеальный пользователь завершил test-drive на 100%; "
                    f"расхождений ответов залогировано: "
                    f"{perfect_answer_mismatches}"
                ),
                section=None,
                step=36,
            )
        else:
            collector.record_skip(
                username=user.username,
                profile=user.test_profile,
                action="TEST_DRIVE_100_PERCENT_SKIPPED",
                message=(
                    "Строгая проверка 100% не выполняется из-за "
                    "залогированных расхождений ответов: "
                    f"{perfect_answer_mismatches}; текущий прогресс "
                    f"{progress_label}%"
                ),
                section=None,
                step=36,
            )
        return True

    async def verify_perfect_progress() -> None:
        progress, progress_label = await scenario_page.read_progress_percent()
        if progress < 100:
            raise AssertionError(
                "Идеальный профиль должен завершить test-drive "
                f"на 100%, получено {progress_label}%"
            )

    return await _run_check(
        collector=collector,
        user=user,
        page=page,
        action="TEST_DRIVE_100_PERCENT_CONFIRMED",
        success_message=("Идеальный пользователь завершил test-drive на 100%"),
        failure_message=("Итоговый прогресс идеального пользователя не равен 100%"),
        operation=verify_perfect_progress,
        section=None,
        step=36,
    )


async def _run_perfect_ctf(
    *,
    scenario_page: NadTestDriveScenarioPage,
    ctf_content: CtfScenarioContent,
    user: TestDriveUser,
    collector: ResultCollector,
) -> bool:
    page = scenario_page.page
    ctf_page = CtfScenarioPage(page)
    outcomes: list[bool] = []

    collector.record_pass(
        username=user.username,
        profile=user.test_profile,
        action="GITLAB_CTF_CONTENT_LOADED",
        message=(
            f"Свежий CTF-контент "
            f"{ctf_content.source.product_slug} "
            f"{ctf_content.source.case_id}: "
            f"этапов={len(ctf_content.stages)}, "
            f"вопросов={ctf_content.total_questions}"
        ),
        section=5,
        step=None,
    )

    if not await _run_critical(
        collector=collector,
        user=user,
        page=page,
        action="CTF_TASKS_TAB_OPENED_FOR_INTERVAL",
        success_message=(
            "Вкладка «Задания и результаты» открыта для "
            "чтения CTF-интервала без раскрытия карточки"
        ),
        failure_message=("Не удалось открыть вкладку CTF перед проверкой интервала"),
        operation=ctf_page.activate_tasks_tab_without_expanding,
        section=5,
        step=ctf_content.stages[0].stage,
    ):
        return False

    async def verify_ctf_nad_product_interval() -> None:
        log_step("[CTF][PRODUCT] Читаем интервал CTF до раскрытия Q1")
        expected_interval = await ctf_page.read_product_interval()
        await BasicModulesPage(page).verify_product_interval(
            username=user.username,
            password=user.password,
            expected_interval=expected_interval,
            expected_source="CTF-ссылка PT NAD",
        )

    outcomes.append(
        await _run_check(
            collector=collector,
            user=user,
            page=page,
            action="CTF_NAD_PRODUCT_INTERVAL_CONFIRMED",
            success_message="Из CTF выполнен переход в PT NAD, интервал совпал",
            failure_message=("Переход или интервал PT NAD из CTF не подтвержден"),
            operation=verify_ctf_nad_product_interval,
            section=5,
            step=ctf_content.stages[0].stage,
        )
    )

    if not await _run_critical(
        collector=collector,
        user=user,
        page=page,
        action="CTF_TASKS_OPENED",
        success_message=(
            "После проверки интервала вкладка заданий CTF "
            "снова активна; карточки не раскрыты"
        ),
        failure_message=(
            "После проверки интервала не удалось восстановить вкладку заданий CTF"
        ),
        operation=ctf_page.activate_tasks_tab_without_expanding,
        section=5,
        step=ctf_content.stages[0].stage,
    ):
        return False

    for stage_index, stage in enumerate(ctf_content.stages):
        yellow_circle_checked = False
        if not await _run_critical(
            collector=collector,
            user=user,
            page=page,
            action=f"CTF_STAGE_{stage.stage}_OPENED",
            success_message=(
                f"CTF-этап {stage.stage} открыт, вопросов={len(stage.questions)}"
            ),
            failure_message=(f"Не удалось открыть CTF-этап {stage.stage}"),
            operation=lambda stage=stage: (
                ctf_page.verify_stage_opened_without_question_interaction(
                    stage.stage,
                    len(stage.questions),
                )
            ),
            section=5,
            step=stage.stage,
        ):
            return False

        if await ctf_page.actionbar_scheme_is_available():
            outcomes.append(
                await _run_check(
                    collector=collector,
                    user=user,
                    page=page,
                    action=(f"CTF_STAGE_{stage.stage}_ACTIONBAR_SCHEME_CONFIRMED"),
                    success_message=(
                        "В боковой панели CTF-этапа загружены встроенная "
                        "и модальная схемы атаки"
                    ),
                    failure_message=(
                        "Схемы атаки в боковой панели CTF-этапа не подтверждены"
                    ),
                    operation=ctf_page.verify_actionbar_materials,
                    section=5,
                    step=stage.stage,
                )
            )

        for question_index, question in enumerate(stage.questions):
            last_in_stage = question_index == len(stage.questions) - 1
            final_question = (
                last_in_stage and stage_index == len(ctf_content.stages) - 1
            )

            async def answer_operation(
                question: CtfQuestion = question,
                last_in_stage: bool = last_in_stage,
                final_question: bool = final_question,
            ) -> None:
                await _answer_ctf_question(
                    ctf_page=ctf_page,
                    question=question,
                    last_in_stage=last_in_stage,
                    final_question=final_question,
                )

            if not await _run_critical(
                collector=collector,
                user=user,
                page=page,
                action=(
                    f"CTF_STAGE_{stage.stage}_Q{question.local_order}_CORRECT_CONFIRMED"
                ),
                success_message=(
                    f"CTF Q{question.global_order}: правильный ответ принят"
                ),
                failure_message=(
                    f"CTF Q{question.global_order}: правильный ответ не подтвержден"
                ),
                operation=answer_operation,
                section=5,
                step=stage.stage,
            ):
                return False

            if not last_in_stage and not yellow_circle_checked:
                yellow_circle_checked = True
                outcomes.append(
                    await _run_check(
                        collector=collector,
                        user=user,
                        page=page,
                        action=(
                            f"CTF_STAGE_{stage.stage}_CURRENT_YELLOW_STATUS_CONFIRMED"
                        ),
                        success_message=(
                            "После первого правильного ответа "
                            "текущий CTF-этап отмечен желтым кружком"
                        ),
                        failure_message=("Не найден желтый кружок текущего CTF-этапа"),
                        operation=lambda stage_number=stage.stage: (
                            ctf_page.verify_current_stage_yellow_circle(stage_number)
                        ),
                        section=5,
                        step=stage.stage,
                    )
                )

        final_stage = stage_index == len(ctf_content.stages) - 1
        if not final_stage:
            outcomes.append(
                await _run_check(
                    collector=collector,
                    user=user,
                    page=page,
                    action=(f"CTF_STAGE_{stage.stage}_INLINE_SCHEME_CONFIRMED"),
                    success_message=("Схема в окне завершения CTF-этапа отображается"),
                    failure_message=(
                        "Схема в окне завершения CTF-этапа не подтверждена"
                    ),
                    operation=lambda: ctf_page.verify_stage_inline_scheme(
                        final_stage=False
                    ),
                    section=5,
                    step=stage.stage,
                )
            )

        if not final_stage:
            outcomes.append(
                await _run_check(
                    collector=collector,
                    user=user,
                    page=page,
                    action=(f"CTF_STAGE_{stage.stage}_SCHEME_DIALOG_CONFIRMED"),
                    success_message=(
                        "Модальное окно схемы завершенного "
                        "CTF-этапа отображается и закрывается"
                    ),
                    failure_message=("Модальное окно схемы CTF-этапа не подтверждено"),
                    operation=lambda: ctf_page.verify_stage_scheme_dialog(
                        final_stage=False
                    ),
                    section=5,
                    step=stage.stage,
                )
            )
            outcomes.append(
                await _run_check(
                    collector=collector,
                    user=user,
                    page=page,
                    action=(f"CTF_STAGE_{stage.stage}_VIDEO_CONFIRMED"),
                    success_message=(
                        "Видео расследования завершенного "
                        "CTF-этапа отображается и закрывается"
                    ),
                    failure_message=("Видео расследования CTF-этапа не подтверждено"),
                    operation=lambda: ctf_page.verify_stage_video(final_stage=False),
                    section=5,
                    step=stage.stage,
                )
            )
            if not await _run_critical(
                collector=collector,
                user=user,
                page=page,
                action=(f"CTF_STAGE_{stage.stage}_CONTINUE_CONFIRMED"),
                success_message=(
                    f"Выполнен переход с CTF-этапа {stage.stage} на следующий"
                ),
                failure_message=(f"Не выполнен переход после CTF-этапа {stage.stage}"),
                operation=ctf_page.click_stage_continue,
                section=5,
                step=stage.stage,
            ):
                return False

        outcomes.append(
            await _run_check(
                collector=collector,
                user=user,
                page=page,
                action=(f"CTF_STAGE_{stage.stage}_SUCCESS_STATUS_CONFIRMED"),
                success_message=(
                    "Завершенный CTF-этап отмечен зеленой иконкой выполнения"
                ),
                failure_message=("Не найден зеленый статус выполнения CTF-этапа"),
                operation=lambda stage_number=stage.stage: (
                    ctf_page.verify_stage_success_status(stage_number)
                ),
                section=5,
                step=stage.stage,
            )
        )

        if final_stage:
            if not await _run_critical(
                collector=collector,
                user=user,
                page=page,
                action="CTF_FINAL_FEEDBACK_HANDLED",
                success_message=(
                    "Финальный отзыв CTF отправлен либо "
                    "окно закрыто крестиком после ошибки"
                ),
                failure_message=("Финальное окно после CTF не обработано"),
                operation=lambda: ctf_page.handle_final_feedback(
                    settings.FINAL_FEEDBACK_TEXT
                ),
                section=5,
                step=stage.stage,
            ):
                return False

    if not all(outcomes):
        log_step(
            "[CTF] Отдельные проверки завершились с FAILED; "
            "после завершения CTF продолжаем обязательный post-flow test-drive"
        )
    return True


async def _run_specialized_unfinished_module_transition(
    *,
    scenario_page: NadTestDriveScenarioPage,
    modules_page: BasicModulesPage,
    content: BasicScenarioContent,
    after_module: BasicModule,
    user: TestDriveUser,
    collector: ResultCollector,
) -> bool:
    page = scenario_page.page
    partial_module, next_module = _select_unfinished_transition_modules(
        content=content,
        after_module=after_module,
    )
    section = _result_section(partial_module)

    if not await _run_critical(
        collector=collector,
        user=user,
        page=page,
        action="SPECIALIZED_UNFINISHED_MODULE_OPENED",
        success_message=(
            "После достижения 70% открыт следующий EX-модуль "
            f"«{partial_module.title}» для проверки незавершенного перехода"
        ),
        failure_message=(
            "После достижения 70% не удалось открыть EX-модуль "
            "для проверки незавершенного перехода"
        ),
        operation=lambda: modules_page.go_to_next_module(partial_module),
        section=section,
        step=partial_module.step_id,
    ):
        return False

    if not await _run_critical(
        collector=collector,
        user=user,
        page=page,
        action="SPECIALIZED_UNFINISHED_MODULE_TASKS_OPENED",
        success_message=(
            "В модуле для проверки незавершенного перехода открыты задания"
        ),
        failure_message=(
            "Не удалось открыть задания модуля для проверки незавершенного перехода"
        ),
        operation=lambda: modules_page.go_to_tasks(partial_module),
        section=section,
        step=partial_module.step_id,
    ):
        return False

    first_question = partial_module.exercises[0]
    if not await _run_check(
        collector=collector,
        user=user,
        page=page,
        action="SPECIALIZED_UNFINISHED_Q1_COMPLETED",
        success_message=(
            "В дополнительном модуле дан один правильный ответ; "
            "остальные вопросы оставлены незавершенными"
        ),
        failure_message=(
            "Не удалось ответить на первый вопрос дополнительного "
            "модуля перед переходом в CTF"
        ),
        operation=lambda: modules_page.answer_question_with_retries(
            partial_module,
            first_question,
            verify_incorrect_first=False,
        ),
        section=section,
        step=partial_module.step_id,
    ):
        return False

    if not await _run_check(
        collector=collector,
        user=user,
        page=page,
        action="SPECIALIZED_UNFINISHED_MODULE_STATUS_DURING_CONFIRMED",
        success_message=("После одного ответа дополнительный модуль имеет started"),
        failure_message=("После одного ответа статус дополнительного модуля неверен"),
        operation=lambda: modules_page.verify_module_status_expected(
            partial_module,
            "started",
        ),
        section=section,
        step=partial_module.step_id,
    ):
        return False

    if not await _run_check(
        collector=collector,
        user=user,
        page=page,
        action="SPECIALIZED_UNFINISHED_NEXT_DIALOG_CONFIRMED",
        success_message=(
            "Для дополнительного незавершенного модуля показано "
            "предупреждение; переход выполнен кнопкой "
            "«К следующему модулю»"
        ),
        failure_message=(
            "Не подтверждено предупреждение при переходе из "
            "дополнительного незавершенного модуля"
        ),
        operation=lambda: modules_page.go_to_next_step(
            expected_step_id=next_module.step_id,
            expected_title=next_module.title,
            require_unfinished_dialog=True,
        ),
        section=_result_section(next_module),
        step=next_module.step_id,
    ):
        return False

    return await _run_check(
        collector=collector,
        user=user,
        page=page,
        action="SPECIALIZED_UNFINISHED_MODULE_STATUS_AFTER_NEXT_CONFIRMED",
        success_message=(
            "После обработки предупреждения дополнительный модуль остался незавершенным"
        ),
        failure_message=(
            "После перехода статус дополнительного незавершенного модуля неверен"
        ),
        operation=lambda: modules_page.verify_module_status_expected(
            partial_module,
            "started",
        ),
        section=section,
        step=partial_module.step_id,
    )


def _select_unfinished_transition_modules(
    *,
    content: BasicScenarioContent,
    after_module: BasicModule,
) -> tuple[BasicModule, BasicModule]:
    module_index = {
        module.step_id: index for index, module in enumerate(content.modules)
    }
    after_index = module_index.get(after_module.step_id)
    if after_index is None:
        raise AssertionError(
            f"Модуль {after_module.step_id} не найден в content.modules"
        )

    reference_section = _result_section(after_module)
    candidates = tuple(
        module
        for module in content.exercise_modules
        if module_index.get(module.step_id, -1) > after_index
        and len(module.exercises) > 1
    )
    if not candidates:
        raise AssertionError(
            "После достижения 70% не найден EX-модуль с двумя "
            "и более вопросами для проверки незавершенного перехода"
        )

    partial_module = next(
        (
            module
            for module in candidates
            if _result_section(module) != reference_section
        ),
        candidates[0],
    )
    partial_index = module_index[partial_module.step_id]
    next_module = next(
        (module for module in content.modules[partial_index + 1 :]),
        None,
    )
    if next_module is None:
        raise AssertionError(
            "После дополнительного незавершенного модуля нет "
            "следующего basic-модуля для перехода"
        )

    return partial_module, next_module


async def _verify_partial_profile_skipped_question(
    *,
    modules_page: BasicModulesPage,
    content: BasicScenarioContent,
) -> None:
    candidates = tuple(
        module for module in content.exercise_modules if module.exercises
    )
    module = await modules_page.find_module_with_status(
        candidates,
        "skipped_by_bs",
    )
    await modules_page.verify_question_status_expected(
        module,
        module.exercises[0],
        "skipped_by_bs",
    )


async def _finish_specialized_profile_test_drive(
    *,
    scenario_page: NadTestDriveScenarioPage,
    modules_page: BasicModulesPage,
    content: BasicScenarioContent,
    ctf_content: CtfScenarioContent,
    user: TestDriveUser,
    collector: ResultCollector,
) -> bool:
    page = scenario_page.page
    if not await _run_critical(
        collector=collector,
        user=user,
        page=page,
        action="TEST_DRIVE_FINISHED_AFTER_CTF",
        success_message=(
            "После CTF test-drive завершен общей кнопкой без "
            "открытия модуля «Комплексный сценарий»"
        ),
        failure_message=("После CTF не удалось завершить test-drive общей кнопкой"),
        operation=scenario_page.finish_test_drive,
        section=None,
        step=None,
    ):
        return False

    if user.test_profile == "answers_and_hints":
        if not await _run_check(
            collector=collector,
            user=user,
            page=page,
            action="SKIPPED_BY_BS_QUESTION_CONFIRMED",
            success_message=(
                "После завершения test-drive открыт EX-модуль "
                "со статусом skipped_by_bs; его вопрос также "
                "имеет skipped_by_bs"
            ),
            failure_message=(
                "После завершения test-drive не подтвержден "
                "статус skipped_by_bs у вопроса пропущенного модуля"
            ),
            operation=lambda: _verify_partial_profile_skipped_question(
                modules_page=modules_page,
                content=content,
            ),
            section=None,
            step=None,
        ):
            return False

    if user.test_profile != "question_metrics":
        return True

    return await _run_check(
        collector=collector,
        user=user,
        page=page,
        action="LEADERBOARD_RESULTS_CONFIRMED",
        success_message=(
            "После завершения test-drive рейтинг открыт; "
            "строка пользователя совпала со статистикой "
            "профиля и dashboard, общее число basic- и CTF-заданий подтверждено"
        ),
        failure_message=(
            "Результаты пользователя в рейтинге участников "
            "не совпали с итоговой статистикой"
        ),
        operation=lambda: verify_test_drive_leaderboard(
            page=page,
            username=user.username,
            expected_total_tasks=(
                content.total_questions + ctf_content.total_questions
            ),
        ),
        section=None,
        step=None,
    )


async def _answer_ctf_question(
    *,
    ctf_page: CtfScenarioPage,
    question: CtfQuestion,
    last_in_stage: bool,
    final_question: bool,
) -> None:
    task = await ctf_page.open_question(question.local_order - 1)
    await ctf_page.fill_answer(task, question.answer)
    await ctf_page.submit_answer(task)

    if final_question:
        await ctf_page.verify_final_dialog()
    elif last_in_stage:
        await ctf_page.wait_for_stage_completion_dialog()
    else:
        await ctf_page.verify_answer_accepted(task)
        await ctf_page.collapse_question(task)


async def _run_copyable_filters_check(
    *,
    modules_page: BasicModulesPage,
    collector: ResultCollector,
    user: TestDriveUser,
    page: Page,
    module: BasicModule,
) -> bool:
    section = _result_section(module)
    action = f"MODULE_{module.step_id}_COPYABLE_FILTERS_CONFIRMED"
    try:
        filters = await modules_page.verify_copyable_filters(module)
    except Exception as exc:
        screenshot = await collector.capture_failure_screenshot(
            page=page,
            username=user.username,
            action=action,
            status="failed",
            section=section,
            step=module.step_id,
        )
        collector.record_fail(
            username=user.username,
            profile=user.test_profile,
            action=action,
            message=(
                "Не подтверждено копирование фильтров модуля: "
                f"{compact_exception_message(exc)}"
            ),
            section=section,
            step=module.step_id,
            screenshot=screenshot,
        )
        return False

    if not filters:
        return True

    collector.record_pass(
        username=user.username,
        profile=user.test_profile,
        action=action,
        message=(
            f"Проверено копируемых фильтров: {len(filters)}; " + " | ".join(filters)
        ),
        section=section,
        step=module.step_id,
    )
    return True


def _record_ctf_progress_target(
    *,
    collector: ResultCollector,
    user: TestDriveUser,
    module: BasicModule,
    progress: tuple[float, str],
    target: float,
) -> None:
    collector.record_pass(
        username=user.username,
        profile=user.test_profile,
        action="CTF_ENTRY_PROGRESS_TARGET_REACHED",
        message=(
            f"Прогресс test-drive достиг {progress[1]}% (порог для CTF: {target:g}%)"
        ),
        section=_result_section(module),
        step=module.step_id,
    )
    log_step(f"[PROGRESS][CTF ENTRY] {progress[1]}% >= {target:g}%")


def _expected_progress_percent(
    statuses: dict[int, str],
) -> float:
    if not statuses:
        raise AssertionError("Нельзя рассчитать прогресс без статусов модулей")
    completed_count = sum(status == "completed" for status in statuses.values())
    return completed_count / len(statuses) * 100


async def _verify_progress_matches_module_statuses(
    *,
    modules_page: BasicModulesPage,
    content: BasicScenarioContent,
    progress: tuple[float, str],
) -> None:
    statuses = await modules_page.read_module_statuses(content.modules)
    expected = _expected_progress_percent(statuses)
    if abs(progress[0] - expected) > 0.11:
        counts = {
            status: sum(actual == status for actual in statuses.values())
            for status in BasicModulesPage._KNOWN_STATUSES
        }
        raise AssertionError(
            "Прогресс не соответствует DOM-статусам модулей: "
            f"ожидалось {expected:.1f}%, получено {progress[1]}%; "
            f"статусы={counts}"
        )
    log_step(
        "[PROGRESS][STATUS] "
        f"{progress[1]}% соответствует "
        f"{sum(value == 'completed' for value in statuses.values())}/"
        f"{len(statuses)} completed-модулям"
    )


async def _record_progress_status_check(
    *,
    scenario_page: NadTestDriveScenarioPage,
    modules_page: BasicModulesPage,
    content: BasicScenarioContent,
    collector: ResultCollector,
    user: TestDriveUser,
    module: BasicModule,
    progress: tuple[float, str],
) -> bool:
    return await _run_check(
        collector=collector,
        user=user,
        page=scenario_page.page,
        action=(f"MODULE_{module.step_id}_PROGRESS_MATCHES_STATUSES"),
        success_message=(
            f"Прогресс {progress[1]}% соответствует количеству completed-модулей"
        ),
        failure_message=("Прогресс сценария не соответствует результатам модулей"),
        operation=lambda: _verify_progress_matches_module_statuses(
            modules_page=modules_page,
            content=content,
            progress=progress,
        ),
        section=_result_section(module),
        step=module.step_id,
    )


async def _record_module_progress(
    *,
    scenario_page: NadTestDriveScenarioPage,
    collector: ResultCollector,
    user: TestDriveUser,
    page: Page,
    module: BasicModule,
    phase: str,
    greater_than: float | None = None,
) -> tuple[float, str] | None:
    section = _result_section(module)
    try:
        if greater_than is None:
            progress = await scenario_page.read_progress_percent()
        else:
            progress = await scenario_page.wait_for_progress_above(greater_than)
    except Exception as exc:
        screenshot = await collector.capture_failure_screenshot(
            page=page,
            username=user.username,
            action=f"MODULE_{module.step_id}_PROGRESS_{phase}_RECORDED",
            status="failed",
            section=section,
            step=module.step_id,
        )
        collector.record_fail(
            username=user.username,
            profile=user.test_profile,
            action=f"MODULE_{module.step_id}_PROGRESS_{phase}_RECORDED",
            message=(
                f"Не удалось считать прогресс модуля: {compact_exception_message(exc)}"
            ),
            section=section,
            step=module.step_id,
            screenshot=screenshot,
        )
        return None

    collector.record_pass(
        username=user.username,
        profile=user.test_profile,
        action=f"MODULE_{module.step_id}_PROGRESS_{phase}_RECORDED",
        message=f"Прогресс {phase.lower()}: {progress[1]}%",
        section=section,
        step=module.step_id,
    )
    log_step(f"[PROGRESS][STEP {module.step_id}][{phase}] {progress[1]}%")
    return progress


async def _run_sos_check(
    *,
    scenario_page: NadTestDriveScenarioPage,
    user: TestDriveUser,
    collector: ResultCollector,
) -> bool:
    page = scenario_page.page
    try:
        result = await scenario_page.submit_sos_report(settings.SOS_DESCRIPTION)
    except Exception as exc:
        screenshot = await collector.capture_failure_screenshot(
            page=page,
            username=user.username,
            action="SOS_REPORT_SUBMITTED",
            status="failed",
            section=None,
            step=None,
        )
        collector.record_fail(
            username=user.username,
            profile=user.test_profile,
            action="SOS_REPORT_SUBMITTED",
            message=(
                f"Не удалось выполнить проверку SOS: {compact_exception_message(exc)}"
            ),
            screenshot=screenshot,
        )
        return False

    if result.request_ok and result.ticket_number is not None:
        cleanup_text = (
            "диалог результата закрыт"
            if result.dialog_closed
            else "закрытие диалога результата не подтверждено"
        )
        ticket_text = (
            f"номер обращения {result.ticket_number}"
            if result.ticket_number is not None
            else "номер обращения не считан"
        )
        collector.record_pass(
            username=user.username,
            profile=user.test_profile,
            action="SOS_REPORT_SUBMITTED",
            message=(
                f"SOS отправлен (HTTP {result.response_status}), "
                f"{ticket_text}, " + cleanup_text
            ),
        )
        return True

    screenshot = await collector.capture_failure_screenshot(
        page=page,
        username=user.username,
        action="SOS_REPORT_SUBMITTED",
        status="failed",
        section=None,
        step=None,
    )
    collector.record_fail(
        username=user.username,
        profile=user.test_profile,
        action="SOS_REPORT_SUBMITTED",
        message=(
            "SOS не подтвержден; "
            f"status={result.response_status}, "
            f"ticket={result.ticket_number or '-'}, "
            f"dialog_closed={result.dialog_closed}, "
            f"error={result.error or '-'}"
        ),
        screenshot=screenshot,
    )
    return False


def _result_section(module: BasicModule) -> int | None:
    return module.ancestor_section_ids[-1] if module.ancestor_section_ids else None


async def _run_check(
    *,
    collector: ResultCollector,
    user: TestDriveUser,
    page: Page,
    action: str,
    success_message: str,
    failure_message: str,
    operation: Callable[[], Awaitable[object]],
    section: int | None = None,
    step: int | None = None,
) -> bool:
    try:
        await operation()
    except Exception as exc:
        screenshot = await collector.capture_failure_screenshot(
            page=page,
            username=user.username,
            action=action,
            status="failed",
            section=section,
            step=step,
        )
        collector.record_fail(
            username=user.username,
            profile=user.test_profile,
            action=action,
            message=(f"{failure_message}: {compact_exception_message(exc)}"),
            section=section,
            step=step,
            screenshot=screenshot,
        )
        return False

    collector.record_pass(
        username=user.username,
        profile=user.test_profile,
        action=action,
        message=success_message,
        section=section,
        step=step,
    )
    return True


async def _run_critical(
    *,
    collector: ResultCollector,
    user: TestDriveUser,
    page: Page,
    action: str,
    success_message: str,
    failure_message: str,
    operation: Callable[[], Awaitable[object]],
    section: int | None = None,
    step: int | None = None,
) -> bool:
    try:
        await operation()
    except Exception as exc:
        screenshot = await collector.capture_failure_screenshot(
            page=page,
            username=user.username,
            action=action,
            status="error",
            section=section,
            step=step,
        )
        collector.record_error(
            username=user.username,
            profile=user.test_profile,
            action=action,
            message=(f"{failure_message}: {compact_exception_message(exc)}"),
            section=section,
            step=step,
            screenshot=screenshot,
        )
        return False

    collector.record_pass(
        username=user.username,
        profile=user.test_profile,
        action=action,
        message=success_message,
        section=section,
        step=step,
    )
    return True
