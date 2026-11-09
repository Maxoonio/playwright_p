import inspect
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
for import_path in (
    TEST_DRIVE_ROOT / "common",
    TEST_DRIVE_ROOT,
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import settings
from ctf_metrics import (
    aggregate_question_metrics,
    answered_question_metrics,
)
from pages.basic_modules_page import ProductInterval
from pages.ctf_scenario_page import CtfScenarioPage
from pages.login_page import LoginPage
from pages.nad_scenario_page import NadTestDriveScenarioPage
from scenarios.runner import (
    _answer_ctf_question,
    _run_full_scenario,
    _run_perfect_ctf,
    _run_sos_check,
)


def test_correct_answer_metrics_match_trainer_formula() -> None:
    metrics = answered_question_metrics(
        attempt_count=1,
        duration_seconds=120,
        actual_seconds=30,
        base_points=15,
    )

    assert metrics.accuracy_percent == 100
    assert metrics.speed_percent == 400
    assert metrics.speed_bonus_points == 3
    assert metrics.awarded_points == 18


def test_aggregate_metrics_use_total_duration_and_time() -> None:
    first = answered_question_metrics(
        attempt_count=1,
        duration_seconds=120,
        actual_seconds=30,
        base_points=10,
    )
    second = answered_question_metrics(
        attempt_count=1,
        duration_seconds=180,
        actual_seconds=60,
        base_points=15,
    )

    aggregate = aggregate_question_metrics([first, second])

    assert aggregate.accuracy_percent == 100
    assert aggregate.speed_percent == 333
    assert aggregate.points == 30


def test_ctf_dom_transition_timeout_is_short() -> None:
    assert settings.CTF_TRANSITION_TIMEOUT_MS == 10_000


def test_app_start_timeout_is_longer_than_regular_dom_timeout() -> None:
    login_source = inspect.getsource(LoginPage.open)
    scenario_source = inspect.getsource(NadTestDriveScenarioPage.open)

    assert settings.APP_START_TIMEOUT_MS >= 120_000
    assert settings.LOGIN_FORM_HIDE_TIMEOUT_MS >= 60_000
    assert "settings.APP_START_TIMEOUT_MS" in login_source
    assert "settings.APP_START_TIMEOUT_MS" in scenario_source


def test_initial_finish_button_matches_current_progress() -> None:
    source = inspect.getsource(NadTestDriveScenarioPage.verify_initial_finish_state)

    assert "settings.FINISH_PROGRESS_THRESHOLD" in source
    assert "to_be_enabled" in source
    assert "to_be_disabled" in source
    assert "kbq-disabled" in source


def test_ctf_flow_uses_collapsed_actionbar_and_final_dom() -> None:
    actionbar_source = inspect.getsource(CtfScenarioPage._ensure_actionbar_open)
    return_source = inspect.getsource(
        NadTestDriveScenarioPage.return_from_ctf_to_basic_modules
    )
    flow_source = inspect.getsource(_run_perfect_ctf)

    assert ".basic-scenario-actionbar-show:visible" in actionbar_source
    assert "автоматический возврат к модулям" in return_source
    assert ".basic-scenario-actionbar-show:visible" in return_source
    assert "verify_final_performance_metrics" not in flow_source
    assert "handle_final_feedback" in flow_source
    assert "FINAL_DIALOG_OPTIONAL" not in flow_source


def test_ctf_flow_checks_optional_entry_scheme_and_closes_material_modals() -> None:
    flow_source = inspect.getsource(_run_perfect_ctf)
    actionbar_materials_source = inspect.getsource(
        CtfScenarioPage.verify_actionbar_materials
    )
    close_source = inspect.getsource(CtfScenarioPage._close_modal_by_button)

    stage_opened = flow_source.index('action=f"CTF_STAGE_{stage.stage}_OPENED"')
    actionbar = flow_source.index("ACTIONBAR_SCHEME_CONFIRMED")
    questions = flow_source.index("for question_index, question in enumerate")

    assert stage_opened < actionbar < questions
    assert "if await ctf_page.actionbar_scheme_is_available()" in flow_source
    assert "operation=ctf_page.verify_actionbar_materials" in flow_source
    assert "verify_actionbar_inline_scheme" in actionbar_materials_source
    assert "verify_actionbar_scheme_dialog" in actionbar_materials_source
    assert "_material_modal_is_closed" in close_source
    assert "Escape" in close_source


def test_perfect_ctf_answers_without_duplicate_metrics_checks() -> None:
    flow_source = inspect.getsource(_run_perfect_ctf)
    answer_source = inspect.getsource(_answer_ctf_question)
    collapse_source = inspect.getsource(CtfScenarioPage.collapse_question)

    assert "verify_completed_question_metrics" not in flow_source
    assert "verify_scenario_metrics" not in flow_source
    assert "read_task_duration_seconds" not in answer_source
    assert "read_submission_elapsed_seconds" not in answer_source
    assert "collapse_question(task)" in answer_source
    assert "scenario-tasks-card__active" in collapse_source
    assert "kbq-chevron-up-s_24" in collapse_source
    assert "_question_is_collapsed" in collapse_source


def test_ctf_flow_checks_stage_statuses_like_trainer() -> None:
    flow_source = inspect.getsource(_run_perfect_ctf)
    yellow_source = inspect.getsource(
        CtfScenarioPage.verify_current_stage_yellow_circle
    )
    success_source = inspect.getsource(CtfScenarioPage.verify_stage_success_status)
    actionbar_source = inspect.getsource(CtfScenarioPage._ensure_actionbar_open)

    assert "CURRENT_YELLOW_STATUS_CONFIRMED" in flow_source
    assert "SUCCESS_STATUS_CONFIRMED" in flow_source
    assert "actionbar__step__circle-yellow" in yellow_source
    assert "success-filled.svg" in success_source
    assert "_actionbar_inline_scheme_image" not in actionbar_source


def test_ctf_product_interval_is_read_from_ctf_context() -> None:
    flow_source = inspect.getsource(_run_perfect_ctf)
    read_source = inspect.getsource(CtfScenarioPage.read_product_interval)
    text_source = inspect.getsource(CtfScenarioPage._read_visible_product_interval_text)
    link_source = inspect.getsource(CtfScenarioPage._read_product_interval_from_link)

    assert "expected_interval = await ctf_page.read_product_interval()" in flow_source
    assert "expected_interval=expected_interval" in flow_source
    assert 'expected_source="CTF-ссылка PT NAD"' in flow_source
    assert "_read_visible_product_interval_text" in read_source
    assert "_read_product_interval_from_link" in read_source
    assert read_source.index("_read_visible_product_interval_text") < read_source.index(
        "_read_product_interval_from_link"
    )
    assert "[id^='matrix_scenarios_task_']:visible" in text_source
    assert ".scenario-tasks-card:visible" in text_source
    assert "from" in link_source
    assert "to" in link_source


@pytest.mark.asyncio
async def test_ctf_product_interval_prefers_visible_ctf_text() -> None:
    page = object.__new__(CtfScenarioPage)
    stale_link_interval = ProductInterval(
        start_date="28.07.2026",
        start_time="13:55",
        end_date="28.07.2026",
        end_time="16:55",
    )
    visible_interval = ProductInterval(
        start_date="29.07.2026",
        start_time="13:47",
        end_date="29.07.2026",
        end_time="16:47",
    )
    page._read_product_interval_from_link = AsyncMock(return_value=stale_link_interval)
    page._read_visible_product_interval_text = AsyncMock(return_value=visible_interval)

    actual = await page.read_product_interval()

    assert actual == visible_interval
    page._read_product_interval_from_link.assert_not_awaited()


def test_perfect_ctf_failed_checks_do_not_skip_required_post_flow() -> None:
    ctf_source = inspect.getsource(_run_perfect_ctf)
    full_flow_source = inspect.getsource(_run_full_scenario)

    assert "if not all(outcomes):" in ctf_source
    assert "return all(outcomes)" not in ctf_source
    assert ctf_source.rstrip().endswith("return True")
    assert full_flow_source.index("_run_perfect_ctf") < full_flow_source.index(
        'action="BASIC_SCENARIO_REOPENED_AFTER_CTF"'
    )
    assert full_flow_source.index(
        'action="BASIC_SCENARIO_REOPENED_AFTER_CTF"'
    ) < full_flow_source.rindex('action="COMPLEX_SCENARIO_MODULE_OPENED"')


def test_full_flow_clicks_complex_finish_before_final_feedback() -> None:
    flow_source = inspect.getsource(_run_full_scenario)
    finish_source = inspect.getsource(
        NadTestDriveScenarioPage.open_final_feedback_from_finish_button
    )
    global_finish_source = inspect.getsource(NadTestDriveScenarioPage.finish_test_drive)
    click_source = inspect.getsource(
        NadTestDriveScenarioPage._click_finish_button_enabled
    )
    wait_source = inspect.getsource(
        NadTestDriveScenarioPage._wait_for_finish_button_enabled
    )

    assert "COMPLEX_SCENARIO_FINAL_DIALOG_OPENED" in flow_source
    assert "open_final_feedback_from_finish_button" in flow_source
    assert "final_feedback_dialog_opened" in flow_source
    assert "COMPLEX_SCENARIO_FINAL_FEEDBACK_ALREADY_HANDLED" in flow_source
    assert "COMPLEX_SCENARIO_REOPENED_VIA_START" in flow_source
    assert "reopen_complex_scenario_via_start" in flow_source
    assert "await scenario_page.open_about_test_drive()" in flow_source
    assert "await modules_page.open_module(complex_module)" in flow_source
    assert "COMPLEX_SCENARIO_MODULE_SCROLLED" in flow_source
    assert "scroll_complex_scenario_module_for_completion" in flow_source
    assert "COMPLEX_SCENARIO_COMPLETION_DWELL_MS" in inspect.getsource(
        NadTestDriveScenarioPage.scroll_complex_scenario_module_for_completion
    )
    assert flow_source.index("COMPLEX_SCENARIO_REOPENED_VIA_START") < flow_source.index(
        "scroll_complex_scenario_module_for_completion"
    )
    assert flow_source.index(
        "scroll_complex_scenario_module_for_completion"
    ) < flow_source.index("open_final_feedback_from_finish_button")
    assert flow_source.index(
        "open_final_feedback_from_finish_button"
    ) < flow_source.index("submit_final_feedback")
    assert "_click_finish_button_enabled" in finish_source
    assert NadTestDriveScenarioPage.FINISH_BUTTON_TEXTS == (
        "Завершить сценарий",
        "Завершить тест-драйв",
    )
    assert "if progress >= 100:" in finish_source
    assert finish_source.index("if progress >= 100:") < finish_source.index(
        "_click_finish_button_enabled"
    )
    assert "_click_finish_button_enabled()" in global_finish_source
    assert "_wait_for_final_feedback_dialog_or_continue_button" in finish_source
    assert "safe_click" not in click_source
    assert "scrollIntoView" not in click_source
    assert 'querySelectorAll("button#finishBtn")' in click_source
    assert "normalize(button.innerText" in click_source
    assert "finishButton.click()" in click_source
    assert "FINISH_BUTTON_TEXTS" in click_source
    assert "button#finishBtn" in wait_source
    assert "!finishButton.disabled" in wait_source
    assert "kbq-disabled" in wait_source


def test_sos_report_waits_for_ticket_number_before_pass() -> None:
    read_source = inspect.getsource(NadTestDriveScenarioPage._read_sos_ticket_number)
    submit_source = inspect.getsource(NadTestDriveScenarioPage.submit_sos_report)
    check_source = inspect.getsource(_run_sos_check)

    assert "timeout=settings.DOM_TIMEOUT_MS" in read_source
    assert "ticket_handle = await self.page.wait_for_function" in read_source
    assert "номер\\\\s+обращения" in read_source
    assert "HTTP успешный, но номер обращения не появился" in submit_source
    assert "result.request_ok and result.ticket_number is not None" in check_source
