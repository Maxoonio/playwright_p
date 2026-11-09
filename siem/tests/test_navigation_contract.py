import inspect
import sys
from pathlib import Path

import pytest

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
for import_path in (
    TEST_DRIVE_ROOT / "common",
    TEST_DRIVE_ROOT,
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from models import BasicModule
from pages.basic_modules_page import BasicModulesPage
from pages.ctf_scenario_page import CtfScenarioPage
from pages.login_page import LoginPage
from pages.siem_scenario_page import (
    APPLICATIONS_TARGET,
    COMPLEX_SCENARIO_TARGET,
    CTF_REQUIRED_MODULES,
    INTRODUCTION_DOM_LINKS,
    USE_CASES_TARGET,
    SiemTestDriveScenarioPage,
)
from scenarios.runner import (
    _run_copyable_filters_check,
    _run_full_scenario,
    _run_perfect_ctf,
    _run_siem_product_interval_check,
    _run_user_in_separate_browser,
)


def test_intro_navigation_targets_match_current_siem_c2_contract() -> None:
    assert USE_CASES_TARGET.target_step_id == "scenario_step_id_5"
    assert USE_CASES_TARGET.target_title == "Работа с PT MC"
    assert APPLICATIONS_TARGET.target_step_id == "scenario_step_id_17"
    assert APPLICATIONS_TARGET.target_title == "Закрепление"
    assert COMPLEX_SCENARIO_TARGET.target_step_id == "scenario_step_id_161"
    assert INTRODUCTION_DOM_LINKS == (
        (
            "Сценарии использования",
            "stepClick('05_mpsiem_td_ptmc_ru.html')",
        ),
        ("Поиск флага (CTF)", "ctfIdClick('c-20')"),
        (
            "Комплексный сценарий",
            "stepClick('33_common_td_complex_ru.html')",
        ),
        (
            "Приложения",
            "stepClick('17_mpsiem_td_appendix_persistence_ru.html')",
        ),
    )


def test_ctf_dialog_requires_three_preparation_modules() -> None:
    assert CTF_REQUIRED_MODULES == (
        "Работа с активами",
        "Работа с событиями",
        "Работа с инцидентами",
    )


def test_basic_module_hints_are_checked_for_perfect_and_hint_profiles() -> None:
    source = inspect.getsource(_run_full_scenario)

    assert 'user.test_profile == "answers_and_hints"' in source
    assert "verify_question_hint" in source
    assert "if False and exercise.hint" not in source
    assert "окно закрыто, прохождение вопроса продолжается" in source


def test_ctf_transition_handles_prerequisites_dialog() -> None:
    transition_source = inspect.getsource(
        SiemTestDriveScenarioPage.verify_ctf_direct_transition
    )
    continue_source = inspect.getsource(
        SiemTestDriveScenarioPage._continue_ctf_from_prerequisites_dialog
    )

    assert "_wait_for_ctf_url_or_prerequisites_dialog" in transition_source
    assert "_continue_ctf_from_prerequisites_dialog" in transition_source
    assert "expect_prerequisites_dialog" in transition_source
    assert "_visible_ctf_section" in transition_source
    assert "open_about_test_drive" not in transition_source
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


def test_go_to_tasks_accepts_already_open_tasks_panel() -> None:
    source = inspect.getsource(BasicModulesPage.go_to_tasks)

    assert "tasks_dom_is_open" in source
    assert "if await tasks_dom_is_open(timeout_ms=750)" in source
    assert "button.is_enabled()" in source
    assert "goToTasksBtn disabled" in source


def test_unavailable_copyable_filters_do_not_stop_profile() -> None:
    source = inspect.getsource(_run_copyable_filters_check)

    assert 'status="skipped"' in source
    assert "collector.record_skip" in source
    assert "Копируемые фильтры недоступны" in source
    assert "В модуле нет доступных copyTextClick-фильтров" not in source
    assert "Отсутствие copyTextClick-блоков" in source
    assert source.count("return True") >= 2


@pytest.mark.asyncio
async def test_module_without_copyable_filters_produces_no_result() -> None:
    events: list[str] = []

    class ModulesPageStub:
        async def verify_copyable_filters(
            self,
            module: BasicModule,
        ) -> tuple[str, ...]:
            return ()

    class CollectorStub:
        async def capture_failure_screenshot(self, **kwargs) -> None:
            events.append("screenshot")

        def record_skip(self, **kwargs) -> None:
            events.append("skip")

        def record_fail(self, **kwargs) -> None:
            events.append("fail")

        def record_pass(self, **kwargs) -> None:
            events.append("pass")

    class UserStub:
        username = "playwright_user_1"
        test_profile = "perfect"

    module = BasicModule(
        step_id=3,
        title="О продукте",
        module_type="ex",
        content_filename=None,
        ancestor_section_ids=(),
        ancestor_section_titles=(),
        required_for_ctf=False,
        exercises=(),
    )

    result = await _run_copyable_filters_check(
        modules_page=ModulesPageStub(),  # type: ignore[arg-type]
        collector=CollectorStub(),  # type: ignore[arg-type]
        user=UserStub(),  # type: ignore[arg-type]
        page=object(),  # type: ignore[arg-type]
        module=module,
    )

    assert result is True
    assert events == []


def test_scenario_profile_is_not_finished_from_global_finally() -> None:
    runner_source = inspect.getsource(_run_user_in_separate_browser)

    assert "TEST_DRIVE_FINISH_RECOVERY" not in runner_source
    assert "ensure_test_drive_finished" not in runner_source


def test_correct_basic_answer_is_retried_until_accepted() -> None:
    answer_source = inspect.getsource(BasicModulesPage.answer_question)
    retry_source = inspect.getsource(
        BasicModulesPage._submit_correct_answer_with_retries
    )

    assert "_submit_correct_answer_with_retries" in answer_source
    assert "BASIC_CORRECT_ANSWER_MAX_ATTEMPTS" in retry_source
    assert "BASIC_CORRECT_ANSWER_RETRY_TIMEOUT_MS" in retry_source
    assert "_prepare_correct_answer" in retry_source
    assert "correct answer was not" in retry_source
    assert "timeout_ms=timeout_ms" in retry_source


def test_infrastructure_module_is_scrolled_inside_progress_flow() -> None:
    flow_source = inspect.getsource(_run_full_scenario)
    scroll_source = inspect.getsource(
        BasicModulesPage.scroll_module_to_bottom_for_completion
    )

    assert "progress_flow_modules" in flow_source
    assert "settings.NEXT_AFTER_PRODUCT_ABOUT_STEP_ID" in flow_source
    assert "INFRASTRUCTURE_MODULE_SCROLLED_TO_BOTTOM" in flow_source
    assert "_run_optional_skip(" in flow_source
    assert "INFRASTRUCTURE_MODULE_NEXT_ARROW_CONFIRMED" in flow_source
    assert "INFRASTRUCTURE_MODULE_BACKEND_COMPLETION_" in flow_source
    assert "scroll_module_to_bottom_for_completion" in flow_source
    assert "next_progress_module_by_step" in flow_source
    assert "modules_page.go_to_next_step" in flow_source
    assert 'phase="AFTER"' in flow_source
    assert "progress_after[0] >= progress_target" in flow_source
    assert "iframe.scenarios-basic-result__iframe" in scroll_source
    assert "candidate.scrollTop = candidate.scrollHeight" in scroll_source
    assert "bottom_gap" in scroll_source


def test_siem_product_credentials_are_taken_from_settings() -> None:
    interval_source = inspect.getsource(BasicModulesPage.verify_product_interval)

    assert '"PRODUCT_LOGIN"' in interval_source
    assert '"PRODUCT_PASSWORD"' in interval_source
    assert "configured_product_username" in interval_source
    assert "configured_product_password" in interval_source
    assert "product_password" in interval_source


def test_siem_product_interval_uses_matrix_text_and_closes_release_notes() -> None:
    interval_source = inspect.getsource(BasicModulesPage.verify_product_interval)
    matrix_source = inspect.getsource(BasicModulesPage._read_matrix_solution_interval)
    login_source = inspect.getsource(BasicModulesPage._login_product_if_needed)
    read_siem_source = inspect.getsource(BasicModulesPage._read_siem_interval_fields)
    expand_siem_source = inspect.getsource(BasicModulesPage._expand_siem_interval_menu)
    wait_fields_source = inspect.getsource(
        BasicModulesPage._wait_for_siem_interval_fields
    )
    release_source = inspect.getsource(
        BasicModulesPage._close_product_release_notes_if_present
    )
    close_external_source = inspect.getsource(BasicModulesPage._close_external_page)

    assert "_read_matrix_solution_interval" in interval_source
    assert "_read_product_interval_from_current_link" not in interval_source
    assert "Временной интервал" in matrix_source
    assert "_close_product_release_notes_if_present" in login_source
    assert "_bypass_product_security_warnings(product_page)" in login_source
    assert "_close_product_release_notes_if_present" in read_siem_source
    assert "_wait_for_siem_product_scope" in read_siem_source
    assert "product_scope" in read_siem_source
    assert "_wait_for_product_login_or_app" not in login_source
    assert "timeout=8_000" in login_source
    assert ".mc-action-bar__left:visible" in login_source
    assert ").first.wait_for(" in login_source
    assert "wait_for_function" not in login_source
    assert ".release-notes-modal:visible" in release_source
    assert "button.mc-modal-close:has(i[mc-icon='mc-close-L_16'])" in (release_source)
    assert "i[mc-icon='mc-close-L_16']" in release_source
    assert ".mc-button-wrapper" in release_source
    assert 'keyboard.press("Escape")' in release_source
    assert "timeout_ms=1_000" in read_siem_source
    assert "fieldset.pt-time-range__range-group:visible" not in (read_siem_source)
    assert "input[name='startTime']:visible" in read_siem_source
    assert "input[name='startDate']:visible" in read_siem_source
    assert "input[name='endTime']:visible" in read_siem_source
    assert "input[name='endDate']:visible" in read_siem_source
    assert "#timeRange > " in expand_siem_source
    assert "span.mc-link.time-range_link[mc-popover='popover']" in (expand_siem_source)
    assert "[template-url='timeRangePopover.html']:visible" in (expand_siem_source)
    assert r"за\s+\d" in expand_siem_source
    assert r"\bза" not in expand_siem_source
    assert "Нажимаем точную DOM-кнопку интервала SIEM" in (expand_siem_source)
    assert "time_range_button.click" in expand_siem_source
    assert "force=True" in expand_siem_source
    assert "dispatchEvent" in expand_siem_source
    assert "input[name='startTime']:visible" in wait_fields_source
    assert "input[name='endDate']:visible" in wait_fields_source
    assert "if not self.page.is_closed()" in close_external_source
    assert "except Exception" in close_external_source


def test_product_security_warnings_continue_until_product_dom_ready() -> None:
    bypass_source = inspect.getsource(
        BasicModulesPage._bypass_product_security_warnings
    )
    ready_source = inspect.getsource(BasicModulesPage._product_page_ready)
    scope_source = inspect.getsource(BasicModulesPage._find_visible_product_scope)
    siem_scope_source = inspect.getsource(BasicModulesPage._wait_for_siem_product_scope)
    diagnostics_source = inspect.getsource(
        BasicModulesPage._product_readiness_diagnostics
    )

    assert "while True" in bypass_source
    assert "_product_page_ready" in bypass_source
    assert "_close_product_release_notes_if_present" in bypass_source
    assert 'settings.PRODUCT_INTERVAL_KIND == "nad"' in bypass_source
    assert "readiness_deadline = None" in bypass_source
    assert "if readiness_deadline is not None" in bypass_source
    assert "продолжаем DOM-ожидание без таймаута" in bypass_source
    assert "_product_readiness_diagnostics" in bypass_source
    assert "range(4)" not in bypass_source
    assert "warning_count" in bypass_source
    assert "input#usernameLocal" in ready_source
    assert ".mc-action-bar__left" in ready_source
    assert "_find_visible_product_scope" in ready_source
    assert "document.querySelector" not in ready_source
    assert "ipn-release-notes-modal" not in ready_source
    assert ".release-notes-modal" not in ready_source
    assert "timeRangeLabels" in diagnostics_source
    assert "loginInputs" in diagnostics_source
    assert "all_text_contents" in diagnostics_source
    assert "_product_dom_scopes" in scope_source
    assert "scope.locator(selector).count()" in scope_source
    assert "вложенный frame" in siem_scope_source


def test_unavailable_siem_product_does_not_stop_matrix_profile() -> None:
    flow_source = inspect.getsource(_run_full_scenario)
    check_source = inspect.getsource(_run_siem_product_interval_check)
    ready_source = inspect.getsource(BasicModulesPage._product_page_ready)

    assert "_run_siem_product_interval_check" in flow_source
    assert "except ProductUnavailableError" in check_source
    assert "_record_skip_with_screenshot" in check_source
    assert "return True" in check_source
    assert "collector.record_fail" in check_source
    assert "raise ProductUnavailableError" in ready_source
    assert "закрылась до загрузки" in ready_source


def test_matrix_login_retries_when_login_page_returns_after_api_200() -> None:
    login_source = inspect.getsource(LoginPage.login)
    gone_source = inspect.getsource(LoginPage._wait_for_login_form_gone)
    visible_source = inspect.getsource(LoginPage.login_form_visible)

    assert "settings.LOGIN_MAX_ATTEMPTS" in login_source
    assert "perform_and_wait_for_response" in login_source
    assert 'path="/api/login/"' in login_source
    assert "_wait_for_login_form_gone" in login_source
    assert "login_form_visible" in gone_source
    assert "снова появилась после успешного" in gone_source
    assert "/login" in visible_source
    assert "wait_for_function" not in gone_source
    assert "wait_for_timeout" not in gone_source


def test_video_tour_timeout_is_non_blocking() -> None:
    video_source = inspect.getsource(SiemTestDriveScenarioPage.verify_video_tour)

    assert "Видеотур не прогрузился полностью" in video_source
    assert "safe_click(close_button)" in video_source
    assert "kbq-progress-bar.actionbar__progress-bar:visible" in video_source
    assert "raise" not in video_source


def test_pro_request_uses_save_button_and_dom_confirmation() -> None:
    submit_source = inspect.getsource(SiemTestDriveScenarioPage.submit_pro_request)
    click_source = inspect.getsource(SiemTestDriveScenarioPage._click_pro_save_button)

    assert "button#proSaveBtn:visible" in submit_source
    assert "_click_pro_save_button(submit)" in submit_source
    assert "remember_pro_response" in submit_source
    assert "_wait_for_pro_submission_dom(dialog)" in submit_source
    assert "submit.click" in click_source
    assert "#proSaveBtn" in click_source


def test_completed_ctf_metrics_reopen_through_expected_dom() -> None:
    reopen_source = inspect.getsource(
        SiemTestDriveScenarioPage.reopen_completed_ctf_from_modules
    )
    tasks_source = inspect.getsource(CtfScenarioPage.open_completed_tasks_and_results)
    tasks_tab_source = inspect.getsource(CtfScenarioPage.tasks_tab)
    return_source = inspect.getsource(
        SiemTestDriveScenarioPage.return_from_ctf_to_basic_modules
    )
    section_source = inspect.getsource(SiemTestDriveScenarioPage._visible_ctf_section)

    assert "_visible_ctf_section" in reopen_source
    assert '"#id_4:visible"' in section_source
    assert "settings.CTF_SECTION_TITLE" in section_source
    assert "Задания\\s+и\\s+результаты" in tasks_tab_source
    assert ".scenarios-tasks-metric-container:visible" in tasks_source
    assert "Вернуться\\s+к\\s+модулям\\s+сценария" in return_source
    assert "basicReady || returnReady || expandReady" in return_source
    assert "timeout=2_000" not in return_source


def test_ctf_tasks_open_by_results_tab_and_current_card_dom() -> None:
    open_source = inspect.getsource(CtfScenarioPage.open_tasks)
    cards_source = inspect.getsource(CtfScenarioPage.visible_task_cards)
    task_source = inspect.getsource(CtfScenarioPage.task_card)
    landing_source = inspect.getsource(SiemTestDriveScenarioPage._wait_for_ctf_landing)

    assert "tasks_tab" in open_source
    assert "_tasks_tab_is_selected" in open_source
    assert "_wait_for_task_cards_visible" in open_source
    assert "Задания и результаты" in open_source
    assert "уже открыта, задания видимы" in open_source
    assert ".scenario-tasks-card:visible" in cards_source
    assert "#matrix_scenarios_task_{question_index}:visible" in task_source
    assert ".scenario-tasks-card:visible" not in task_source
    assert ".nth(question_index)" not in task_source
    assert "Задания\\\\s+и\\\\s+результаты" in landing_source
    assert ".scenario-tasks-card" in landing_source


def test_perfect_ctf_checks_product_interval_before_opening_first_card() -> None:
    source = inspect.getsource(_run_perfect_ctf)

    tasks_tab = source.index('action="CTF_TASKS_TAB_OPENED_FOR_INTERVAL"')
    interval = source.index('action="CTF_SIEM_PRODUCT_INTERVAL_CONFIRMED"')
    tasks_ready = source.index('action="CTF_TASKS_OPENED"', interval)
    first_answer = source.index("await _answer_ctf_question(")

    assert tasks_tab < interval < tasks_ready < first_answer
    assert source.count("ctf_page.activate_tasks_tab_without_expanding") == 2
    assert "verify_stage_opened_without_question_interaction" in source
    assert "operation=ctf_page.open_tasks" not in source


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
    scenario_page = SiemTestDriveScenarioPage(page)

    await scenario_page._wait_for_ctf_url_or_prerequisites_dialog("c-20")
    await scenario_page._wait_for_ctf_url_or_anyway_button("c-20")

    assert [call["arg"] for call in page.calls] == ["c-20", "c-20"]
