import inspect
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
for import_path in (
    TEST_DRIVE_ROOT / "common",
    TEST_DRIVE_ROOT,
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from pages.basic_modules_page import BasicModulesPage
from pages.ctf_scenario_page import CtfScenarioPage
from pages.nad_scenario_page import (
    COMPLEX_SCENARIO_TARGET,
    CTF_REQUIRED_MODULES,
    USE_CASES_TARGET,
    WEB_INTERFACE_TARGET,
    NadTestDriveScenarioPage,
)
from scenarios.runner import _run_full_scenario, _run_perfect_ctf


@pytest.mark.asyncio
async def test_basic_preparation_reuses_test_drive_introduction_flow() -> None:
    scenario_page = NadTestDriveScenarioPage(page=object())
    intro_link = object()
    scenario_page._visible_intro_link = Mock(return_value=intro_link)
    scenario_page.open_sidebar_step = AsyncMock()

    await scenario_page.open_about_test_drive()

    scenario_page.open_sidebar_step.assert_awaited_once_with(
        step_id=scenario_page.ABOUT_TEST_DRIVE_STEP_ID,
        title="О базовой подготовке",
        accepted_titles=("О базовой подготовке", "О Тест-драйве"),
        ready_locator=intro_link,
    )


class _FinishButtonLocator:
    def __init__(self) -> None:
        self.text_pattern = None

    def filter(self, *, has_text):
        self.text_pattern = has_text
        return self

    @property
    def first(self):
        return self


def test_finish_button_accepts_basic_preparation_and_test_drive_labels() -> None:
    locator = _FinishButtonLocator()
    page = Mock()
    page.locator.return_value = locator
    scenario_page = NadTestDriveScenarioPage(page=page)

    assert scenario_page._finish_button_locator() is locator
    assert locator.text_pattern.fullmatch("Завершить сценарий")
    assert locator.text_pattern.fullmatch("Завершить тест-драйв")
    assert not locator.text_pattern.fullmatch("Завершить CTF")


def test_intro_navigation_targets_match_current_nad_c3_contract() -> None:
    assert WEB_INTERFACE_TARGET.target_title == "Главное меню"
    assert USE_CASES_TARGET.target_step_id == "scenario_step_id_12"
    assert (
        USE_CASES_TARGET.target_title == "Поиск передачи учётных данных в открытом виде"
    )
    assert COMPLEX_SCENARIO_TARGET.target_step_id == "scenario_step_id_36"


def test_ctf_dialog_requires_four_preparation_modules() -> None:
    assert CTF_REQUIRED_MODULES == (
        "Работа с Атаками",
        "Фильтрация данных по метаданным трафика",
        "Верификация угроз",
        "Практические рекомендации по расследованию",
    )


def test_basic_module_hints_are_checked_for_perfect_and_hint_profiles() -> None:
    source = inspect.getsource(_run_full_scenario)

    assert 'user.test_profile == "answers_and_hints"' in source
    assert "verify_question_hint" in source
    assert "if False and exercise.hint" not in source
    assert "окно закрыто, прохождение вопроса продолжается" in source


def test_ctf_transition_handles_prerequisites_dialog() -> None:
    transition_source = inspect.getsource(
        NadTestDriveScenarioPage.verify_ctf_direct_transition
    )
    continue_source = inspect.getsource(
        NadTestDriveScenarioPage._continue_ctf_from_prerequisites_dialog
    )

    assert "_wait_for_ctf_url_or_prerequisites_dialog" in transition_source
    assert "_continue_ctf_from_prerequisites_dialog" in transition_source
    assert "expect_prerequisites_dialog" in transition_source
    assert "Все required_for_ctf-модули completed" in transition_source
    assert "matrix-ctf-table-dialog:visible" in continue_source
    assert "_ctf_prerequisites_continue_button" in continue_source


def test_final_skipped_question_is_checked_by_dom_status() -> None:
    find_source = inspect.getsource(BasicModulesPage.find_module_with_status)
    verify_source = inspect.getsource(BasicModulesPage.verify_question_status_expected)
    status_source = inspect.getsource(BasicModulesPage._read_status)

    assert "reversed(modules)" in find_source
    assert "await self.ensure_module_for_answers(module)" in verify_source
    assert "await self._wait_for_status(card, expected_status)" in (verify_source)
    assert '"forbidden.svg"' in status_source


def test_video_tour_timeout_is_non_blocking() -> None:
    video_source = inspect.getsource(NadTestDriveScenarioPage.verify_video_tour)

    assert "Видеотур не прогрузился полностью" in video_source
    assert "safe_click(close_button)" in video_source
    assert "kbq-progress-bar.actionbar__progress-bar:visible" in video_source
    assert "raise" not in video_source


@pytest.mark.parametrize("route", ("dashboards/main", "alerts/list"))
def test_nad_staging_product_routes_expose_product_interval(route: str) -> None:
    start = datetime(2026, 10, 5, 12, 37)
    end = start + timedelta(hours=1)
    url = (
        f"https://192.168.91.12/#/{route}"
        f"?from={int(start.timestamp() * 1_000)}"
        f"&to={int(end.timestamp() * 1_000)}&sources=2"
    )

    actual = BasicModulesPage(None)._read_nad_interval_from_url(url)

    assert actual is not None
    assert actual.start_date == "05.10.2026"
    assert actual.start_time == "12:37"
    assert actual.end_date == "05.10.2026"
    assert actual.end_time == "13:37"
    assert (
        BasicModulesPage(None)._read_nad_interval_from_url(
            "https://192.168.91.12/#/login"
        )
        is None
    )


def test_nad_product_readiness_accepts_dashboard_and_is_non_blocking() -> None:
    verification_source = inspect.getsource(BasicModulesPage.verify_product_interval)
    ready_source = inspect.getsource(BasicModulesPage._product_page_ready)
    scope_source = inspect.getsource(BasicModulesPage._nad_dashboard_scope)
    interval_source = inspect.getsource(BasicModulesPage._read_nad_interval_fields)
    bypass_source = inspect.getsource(
        BasicModulesPage._bypass_product_security_warnings
    )
    flow_source = inspect.getsource(_run_full_scenario)

    assert "_nad_dashboard_scope" in ready_source
    assert "document.readyState" in scope_source
    assert "header:visible nf-header mc-navbar:visible" in scope_source
    assert "a#header-menu-item-dashboards:visible" in scope_source
    assert "Дашборды" in scope_source
    assert "_read_nad_interval_from_url" in interval_source
    assert "Интервал прочитан из DOM-полей" in interval_source
    assert "from/to из URL маршрута" in interval_source
    assert "PRODUCT_READINESS_TIMEOUT_MS" in bypass_source
    assert verification_source.count("_open_product_page(product_link)") == 2
    assert "nad_auth_only" in verification_source
    assert "wait_for_app_after_submit=not nad_auth_only" in verification_source
    assert "PRODUCT_FIRST_LOGIN_SETTLE_MS" in verification_source
    assert "await first_page.wait_for_timeout(settle_ms)" in verification_source
    assert "Первая авторизация завершена" not in verification_source
    assert "Matrix перед повторным" in verification_source
    assert verification_source.count("_read_matrix_solution_interval()") == 2
    assert "Первый переход завершен" in verification_source
    assert verification_source.index("first_page") < verification_source.index(
        "second_page"
    )

    action_index = flow_source.index('action="NAD_PRODUCT_INTERVAL_CONFIRMED"')
    interval_flow = flow_source[action_index - 250 : action_index + 650]
    assert "await _run_check(" in interval_flow
    assert "await _run_critical(" not in interval_flow


def test_pro_request_uses_save_button_and_dom_confirmation() -> None:
    submit_source = inspect.getsource(NadTestDriveScenarioPage.submit_pro_request)
    click_source = inspect.getsource(NadTestDriveScenarioPage._click_pro_save_button)

    assert "button#proSaveBtn:visible" in submit_source
    assert "_click_pro_save_button(submit)" in submit_source
    assert "remember_pro_response" in submit_source
    assert "_wait_for_pro_submission_dom(dialog)" in submit_source
    assert "submit.click" in click_source
    assert "#proSaveBtn" in click_source


def test_completed_ctf_metrics_reopen_through_expected_dom() -> None:
    reopen_source = inspect.getsource(
        NadTestDriveScenarioPage.reopen_completed_ctf_from_modules
    )
    tasks_source = inspect.getsource(CtfScenarioPage.open_completed_tasks_and_results)
    tasks_tab_source = inspect.getsource(CtfScenarioPage.tasks_tab)
    return_source = inspect.getsource(
        NadTestDriveScenarioPage.return_from_ctf_to_basic_modules
    )

    assert '"#id_5:visible"' in reopen_source
    assert "Расследование\\s+сценария" in reopen_source
    assert "Задания\\s+и\\s+результаты" in tasks_tab_source
    assert ".scenarios-tasks-metric-container:visible" in tasks_source
    assert "Вернуться\\s+к\\s+модулям\\s+сценария" in return_source
    assert "basicReady || returnReady || expandReady" in return_source
    assert "timeout=2_000" not in return_source


def test_ctf_landing_accepts_already_open_tasks_tab() -> None:
    landing_source = inspect.getsource(NadTestDriveScenarioPage._wait_for_ctf_landing)
    open_source = inspect.getsource(CtfScenarioPage.open_tasks)
    cards_source = inspect.getsource(CtfScenarioPage.visible_task_cards)

    assert "Задания\\\\s+и\\\\s+результаты" in landing_source
    assert ".scenario-tasks-card" in landing_source
    assert "_tasks_tab_is_selected" in open_source
    assert "_wait_for_task_cards_visible" in open_source
    assert "уже открыта, задания видимы" in open_source
    assert ".scenario-tasks-card:visible" in cards_source


def test_perfect_ctf_checks_product_interval_before_opening_first_card() -> None:
    source = inspect.getsource(_run_perfect_ctf)

    tasks_tab = source.index('action="CTF_TASKS_TAB_OPENED_FOR_INTERVAL"')
    interval = source.index('action="CTF_NAD_PRODUCT_INTERVAL_CONFIRMED"')
    tasks_ready = source.index('action="CTF_TASKS_OPENED"', interval)
    first_answer = source.index("await _answer_ctf_question(")

    assert tasks_tab < interval < tasks_ready < first_answer
    assert "activate_tasks_tab_without_expanding" in source
    assert "verify_stage_opened_without_question_interaction" in source
    assert "operation=ctf_page.open_tasks" not in source


def test_perfect_ctf_does_not_duplicate_question_metrics_profile() -> None:
    source = inspect.getsource(_run_perfect_ctf)

    assert "AGGREGATE_METRICS_CONFIRMED" not in source
    assert "verify_scenario_metrics" not in source
    assert "verify_final_performance_metrics" not in source
    assert "read_task_duration_seconds" not in source


class _WaitForFunctionPage:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def wait_for_function(
        self,
        expression: str,
        *,
        arg: object = None,
        timeout: float | None = None,
    ) -> None:
        self.calls.append(
            {
                "expression": expression,
                "arg": arg,
                "timeout": timeout,
            }
        )


@pytest.mark.asyncio
async def test_ctf_transition_waits_pass_case_id_as_keyword_arg() -> None:
    page = _WaitForFunctionPage()
    scenario_page = NadTestDriveScenarioPage(page)

    await scenario_page._wait_for_ctf_url_or_prerequisites_dialog("c-10")
    await scenario_page._wait_for_ctf_url_or_anyway_button("c-10")

    assert [call["arg"] for call in page.calls] == ["c-10", "c-10"]
