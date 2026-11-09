from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
for import_path in (
    TEST_DRIVE_ROOT / "common",
    TEST_DRIVE_ROOT,
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import pages.basic_modules_page as basic_modules_page_module
import pages.sandbox_scenario_page as sandbox_scenario_page_module
import scenarios.runner as sandbox_runner
import settings
from ctf_metrics import QuestionMetricsExpectation
from models import BasicExercise, BasicModule, CtfQuestion
from models import TestDriveUser as DriveUser
from pages.basic_modules_page import (
    BasicModulesPage,
    QuestionAnswerRejectedError,
    normalize_windows_path_escaping,
)
from pages.ctf_scenario_page import CtfScenarioPage
from pages.sandbox_scenario_page import (
    COMPLEX_SCENARIO_TARGET,
    CTF_REQUIRED_MODULE_STEP_IDS,
    CTF_REQUIRED_MODULES,
    INTRODUCTION_DOM_LINKS,
    MALWARE_CAPABILITIES_TARGET,
    USE_CASES_TARGET,
    WEB_INTERFACE_TARGET,
    SandboxTestDriveScenarioPage,
)
from scenarios.runner import (
    _answer_ctf_question,
    _ensure_specialized_test_drive_finished,
    _finish_specialized_profile_test_drive,
    _run_copyable_filters_check,
    _run_full_scenario,
    _run_perfect_ctf,
    _run_shared_introduction,
)


class _FieldExpectation:
    def __init__(self, field) -> None:
        self.field = field

    async def to_have_value(self, expected: str, **_kwargs) -> None:
        assert self.field.value == expected

    async def to_be_visible(self, **_kwargs) -> None:
        return None

    async def to_be_enabled(self, **_kwargs) -> None:
        return None


class _AppendingField:
    def __init__(self, value: str) -> None:
        self.value = value
        self.calls: list[str] = []
        self.selected = False

    @property
    def first(self):
        return self

    async def fill(self, value: str) -> None:
        self.calls.append(f"fill:{value}")
        self.value = value
        self.selected = False

    async def scroll_into_view_if_needed(self) -> None:
        self.calls.append("scroll")

    async def click(self, **_kwargs) -> None:
        self.calls.append("click")

    async def focus(self) -> None:
        self.calls.append("focus")

    async def press(self, key: str) -> None:
        self.calls.append(key)

    async def select_text(self) -> None:
        self.calls.append("select_text")
        self.selected = True

    async def press_sequentially(self, value: str, **_kwargs) -> None:
        self.calls.append("press_sequentially")
        self.value = value if self.selected else self.value + value
        self.selected = False

    async def evaluate(self, _script: str, arg=None) -> None:
        if arg is None:
            self.calls.append("sync")
        else:
            self.calls.append("native")
            self.value = arg

    async def input_value(self, **_kwargs) -> str:
        return self.value


class _InputCard:
    def __init__(self, field: _AppendingField) -> None:
        self.field = field

    def locator(self, _selector: str) -> _AppendingField:
        return self.field


class _UnicodeRejectingField(_AppendingField):
    async def fill(self, value: str) -> None:
        self.calls.append(f"fill:{value}")
        if value.isascii():
            self.value = value

    async def press_sequentially(self, value: str, **_kwargs) -> None:
        self.calls.append("press_sequentially_rejected")


class _SequentialUnicodeField(_UnicodeRejectingField):
    async def press_sequentially(self, value: str, **_kwargs) -> None:
        self.calls.append("press_sequentially")
        self.value = value if self.selected else self.value + value
        self.selected = False


class _ClickBlockedField(_UnicodeRejectingField):
    async def click(self, **_kwargs) -> None:
        self.calls.append("click_blocked")
        raise TimeoutError("answer field is covered by a stale overlay")


class _Keyboard:
    def __init__(self, field: _AppendingField, *, accepts_text: bool = True) -> None:
        self.field = field
        self.accepts_text = accepts_text

    async def insert_text(self, value: str) -> None:
        self.field.calls.append("insert_text")
        if self.accepts_text:
            self.field.value = (
                value if self.field.selected else self.field.value + value
            )
            self.field.selected = False


class _KeyboardPage:
    def __init__(self, field: _AppendingField, *, accepts_text: bool = True) -> None:
        self.keyboard = _Keyboard(field, accepts_text=accepts_text)

    async def wait_for_timeout(self, _timeout_ms: int) -> None:
        return None


def test_intro_links_open_first_module_of_each_sandbox_section() -> None:
    assert (
        WEB_INTERFACE_TARGET.target_step_id,
        WEB_INTERFACE_TARGET.document_name,
    ) == ("scenario_step_id_5", "11560197387_ru.html")
    assert (
        MALWARE_CAPABILITIES_TARGET.target_step_id,
        MALWARE_CAPABILITIES_TARGET.document_name,
    ) == ("scenario_step_id_17", "11583685387_ru.html")
    assert (
        USE_CASES_TARGET.target_step_id,
        USE_CASES_TARGET.document_name,
    ) == ("scenario_step_id_21", "11583691147_ru.html")
    assert (
        COMPLEX_SCENARIO_TARGET.target_step_id,
        COMPLEX_SCENARIO_TARGET.document_name,
    ) == ("scenario_step_id_161", "33_common_td_complex_ru.html")
    assert len(INTRODUCTION_DOM_LINKS) == 7


@pytest.mark.asyncio
async def test_next_module_uses_shared_safe_opening_flow() -> None:
    module = BasicModule(
        step_id=3,
        title="О PT Sandbox",
        module_type="ex",
        content_filename="11538304011_ru.html",
        ancestor_section_ids=(1,),
        ancestor_section_titles=("Введение",),
        required_for_ctf=False,
        exercises=(),
    )
    modules_page = BasicModulesPage(page=object())
    modules_page._module_is_active = AsyncMock(return_value=False)
    modules_page.open_module = AsyncMock()

    await modules_page.go_to_next_module(module)

    modules_page.open_module.assert_awaited_once_with(module)


@pytest.mark.asyncio
async def test_active_module_does_not_require_visible_left_tree_item() -> None:
    module = BasicModule(
        step_id=3,
        title="О PT Sandbox",
        module_type="ex",
        content_filename="11538304011_ru.html",
        ancestor_section_ids=(1,),
        ancestor_section_titles=("Введение",),
        required_for_ctf=False,
        exercises=(),
    )
    modules_page = BasicModulesPage(page=object())
    modules_page.module_step = Mock(return_value=object())
    modules_page._module_is_active = AsyncMock(return_value=True)
    modules_page._wait_for_module_active = AsyncMock()
    modules_page._verify_module_iframe_if_available = AsyncMock()
    modules_page._ensure_module_visible = AsyncMock()

    await modules_page.open_module(module)

    modules_page._wait_for_module_active.assert_awaited_once_with(
        3,
        "О PT Sandbox",
    )
    modules_page._verify_module_iframe_if_available.assert_awaited_once_with(module)
    modules_page._ensure_module_visible.assert_not_awaited()


def test_hidden_module_status_is_read_from_attached_dom() -> None:
    module_status_source = inspect.getsource(BasicModulesPage.read_module_status)
    read_source = inspect.getsource(BasicModulesPage._read_status)
    wait_source = inspect.getsource(BasicModulesPage._wait_for_allowed_status)

    assert "if await root.count() == 0:" in module_status_source
    assert "await self._ensure_module_visible(module)" in module_status_source
    assert "to_be_attached" in read_source
    assert "to_be_visible" not in read_source
    assert "to_be_attached" in wait_source


def test_stage_status_progress_mismatch_does_not_block_module_navigation() -> None:
    source = inspect.getsource(_run_full_scenario)

    assert "check_module_statuses and not await _record_progress_status_check" not in (
        source
    )
    assert source.count("await _record_progress_status_check(") == 3


def test_sandbox_ctf_contract_uses_current_required_modules() -> None:
    assert CTF_REQUIRED_MODULES == (
        "Страница Объекты",
        "Страница Источники",
        "Статический анализ",
        "Поведенческий анализ",
        "Анализ письма с вредоносным архивом",
    )
    assert CTF_REQUIRED_MODULE_STEP_IDS == (8, 10, 17, 18, 21)
    assert settings.CTF_CASE_ID == "c-10"
    assert settings.CTF_SECTION_ID == 5


def test_ctf_prerequisites_navigation_uses_ctf_section_dom() -> None:
    source = inspect.getsource(
        SandboxTestDriveScenarioPage.verify_ctf_prerequisites_dialog
    )
    section_source = inspect.getsource(
        SandboxTestDriveScenarioPage._visible_ctf_section
    )

    assert "_visible_ctf_section" in source
    assert "open_about_test_drive" in source
    assert "matrix-ctf-table-dialog:visible" in source
    assert "settings.CTF_SECTION_ID" in section_source
    assert "settings.CTF_SECTION_TITLE" in section_source


def test_users_two_to_five_enter_ctf_at_seventy_percent() -> None:
    flow_source = inspect.getsource(_run_full_scenario)
    runner_source = (TEST_DRIVE_ROOT / "scenarios" / "runner.py").read_text(
        encoding="utf-8"
    )

    assert "SPECIALIZED_CTF_ENTRY_PROGRESS_PERCENT" in flow_source
    assert "progress_target" in flow_source
    assert "run_specialized_ctf_profile" in flow_source
    assert "_finish_specialized_profile_test_drive" in flow_source
    assert "ensure_test_drive_finished" in runner_source
    assert "SKIPPED_BY_BS_QUESTION_CONFIRMED" in runner_source


def test_sandbox_product_interval_uses_exact_ag_grid_dom() -> None:
    verify_source = inspect.getsource(BasicModulesPage.verify_product_interval)
    verify_page_source = inspect.getsource(
        BasicModulesPage._verify_product_interval_on_page
    )
    read_source = inspect.getsource(BasicModulesPage._read_sandbox_interval_fields)
    wait_source = inspect.getsource(BasicModulesPage._wait_for_sandbox_product_scope)
    expand_source = inspect.getsource(BasicModulesPage._expand_sandbox_interval_menu)

    assert "_verify_product_interval_on_page" in verify_source
    assert 'PRODUCT_INTERVAL_KIND == "sandbox"' in verify_page_source
    assert "_read_sandbox_interval_fields" in verify_page_source
    for source in (wait_source, expand_source):
        assert ".ag-header-cell[col-id='time']:visible" in source
        assert "date-time-range-filter .filter-popover:visible" in source
    assert "Дата создания" in expand_source
    assert "trigger.click" in expand_source
    assert "force=True" in expand_source
    assert "dispatchEvent" in expand_source
    assert "#radioPeriodRange:visible" in expand_source
    assert "kbq-selected" in expand_source
    for field_name in (
        "startDate",
        "startDateTime",
        "endDate",
        "endDateTime",
    ):
        assert f"input[name='{field_name}']:visible" in read_source


def test_product_link_host_is_taken_from_runtime_matrix_link() -> None:
    source = inspect.getsource(BasicModulesPage._verify_product_link_contract)

    assert "configured_host" in source
    assert "if configured_host" in source
    assert "parsed.hostname == urlsplit(settings.BASE_URL).hostname" in source


def test_copyable_filters_trust_active_module_and_fresh_toast_mutation() -> None:
    verify_source = inspect.getsource(BasicModulesPage.verify_copyable_filters)
    toast_source = inspect.getsource(BasicModulesPage._wait_for_new_copy_toast)

    assert "await self.open_module(module)" in verify_source
    assert "to_have_attribute" not in verify_source
    assert "changedAfterStart" in toast_source
    assert "hiddenAfterStart" in toast_source


@pytest.mark.asyncio
async def test_basic_correct_input_replaces_rejected_value(monkeypatch) -> None:
    modules_page = BasicModulesPage(page=object())
    field = _AppendingField("rejected")
    monkeypatch.setattr(
        basic_modules_page_module,
        "expect",
        lambda target: _FieldExpectation(target),
    )

    await modules_page._replace_text_field_value(field, "accepted")

    assert field.value == "accepted"
    assert field.calls == [
        "scroll",
        "click",
        "focus",
        "fill:accepted",
        "sync",
    ]


@pytest.mark.parametrize(
    ("answer", "wrong"),
    [
        ("UA", "XX"),
        ("ua", "xx"),
        ("Троян", "Яяяюя"),
        ("троян", "яяяюя"),
        ("XxЯя", "YyЮю"),
        ("A-1", "X-2"),
    ],
)
def test_wrong_input_preserves_answer_character_case(
    answer: str,
    wrong: str,
) -> None:
    modules_page = BasicModulesPage(page=object())

    actual = modules_page._wrong_input_value(answer)

    assert actual == wrong
    assert len(actual) == len(answer)
    assert actual != answer


def test_sandbox_accepts_only_known_trailing_windows_separator_normalization() -> None:
    expected = "C:\\sponge-bob\\exe-zip-injector\\"

    assert BasicModulesPage._text_field_value_matches(expected, expected)
    assert BasicModulesPage._text_field_value_matches(expected[:-1], expected)
    assert not BasicModulesPage._text_field_value_matches(expected[:-2], expected)
    assert not BasicModulesPage._text_field_value_matches("ответ", "ответ/")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "C:\\\\sponge-bob\\\\exe-zip-injector\\\\",
            "C:\\sponge-bob\\exe-zip-injector\\",
        ),
        (
            "C:\\sponge-bob\\exe-zip-injector\\",
            "C:\\sponge-bob\\exe-zip-injector\\",
        ),
        ("C:\\sponge-bob\\\\payload", "C:\\sponge-bob\\payload"),
        (r"\\server\\share", r"\\server\\share"),
        (r"\\d+\\w+", r"\\d+\\w+"),
        ("обычный ответ", "обычный ответ"),
    ],
)
def test_windows_path_escape_normalizer_is_safe_and_idempotent(
    source: str,
    expected: str,
) -> None:
    assert normalize_windows_path_escaping(source) == expected
    assert normalize_windows_path_escaping(expected) == expected


def test_perfect_ctf_runs_preflight_before_opening_first_task() -> None:
    source = inspect.getsource(_run_perfect_ctf)

    description = source.index('action="CTF_DESCRIPTION_OPENED"')
    tasks_tab = source.index('action="CTF_TASKS_TAB_OPENED_FOR_INTERVAL"')
    interval = source.index('action="CTF_SANDBOX_PRODUCT_INTERVAL_CONFIRMED"')
    tasks_ready = source.index('action="CTF_TASKS_OPENED"')
    stage = source.index('action=f"CTF_STAGE_{stage.stage}_OPENED"')
    actionbar = source.index("ACTIONBAR_SCHEME_CONFIRMED")
    questions = source.index("for question_index, question in enumerate")

    assert description < tasks_tab < interval < tasks_ready
    assert tasks_ready < stage < actionbar < questions
    assert "_run_sos_check(" not in source
    assert source.count("operation=ctf_page.activate_tasks_tab_without_expanding") == 2
    assert "operation=ctf_page.open_tasks" not in source
    assert "read_sandbox_ctf_product_interval()" in source
    assert "product_link=ctf_page.sandbox_ctf_product_link()" in source


def test_ctf_interval_is_read_from_tasks_tab_before_card_expansion() -> None:
    description_source = inspect.getsource(CtfScenarioPage.open_description)
    activation_source = inspect.getsource(
        CtfScenarioPage.activate_tasks_tab_without_expanding
    )
    interval_source = inspect.getsource(CtfScenarioPage.read_product_interval)
    text_source = inspect.getsource(CtfScenarioPage._read_visible_product_interval_text)
    link_source = inspect.getsource(CtfScenarioPage.product_link)

    assert "kbq-selected" in description_source
    assert "product_link" not in description_source
    assert "_tasks_tab_is_selected" in activation_source
    assert "_wait_for_task_cards_visible" in activation_source
    assert "повторный клик не требуется" in activation_source
    assert "safe_click(tab)" in activation_source
    assert "visible_task_cards().first" in activation_source
    assert "_ensure_first_unresolved_task_expanded" not in activation_source
    assert "_read_visible_product_interval_text()" in interval_source
    assert "[id^='matrix_scenarios_task_']:visible" in text_source
    assert "kbq-tab-body.kbq-tab-body__active:visible" in text_source
    assert "a[target='_blank'].statistics-link-cursor:visible" in link_source


def test_sandbox_ctf_interval_is_scoped_to_its_exact_product_link() -> None:
    source = inspect.getsource(CtfScenarioPage.read_sandbox_ctf_product_interval)
    flow_source = inspect.getsource(_run_perfect_ctf)
    content_source = inspect.getsource(CtfScenarioPage.sandbox_ctf_tasks_content)
    link_source = inspect.getsource(CtfScenarioPage.sandbox_ctf_product_link)

    assert "self.sandbox_ctf_tasks_content()" in source
    assert "self.sandbox_ctf_product_link()" in source
    assert "div.ng-star-inserted:visible > div:visible" in source
    assert "all_inner_texts" in source
    assert "element.parentElement" not in source
    assert "Временной\\s+интервал\\s+для\\s+поиска\\s+решения" in source
    assert "_read_visible_product_interval_text" not in source
    assert "_parse_sandbox_product_interval_href" in source
    assert "kbq-tab-body.kbq-tab-body__active:visible" in content_source
    assert "sandbox_ctf_tasks_content()" in link_source
    assert "statistics-link-cursor:visible" in link_source
    assert "read_sandbox_ctf_product_interval()" in flow_source
    assert "product_link=ctf_page.sandbox_ctf_product_link()" in flow_source


def test_sandbox_ctf_href_interval_uses_nested_filters_time_range() -> None:
    page = CtfScenarioPage(SimpleNamespace())
    interval = page._parse_sandbox_product_interval_href(
        "https://192.168.91.14/tasks?filters=%7B%22time%22%3A%7B%22periodId%22"
        "%3A%22range%22%2C%22range%22%3A%7B%22startTimestamp%22%3A"
        "1789205319234%2C%22endTimestamp%22%3A1789216119234%7D%7D%7D"
    )

    assert interval is not None
    assert str(interval) == "12.09.2026 12:28 - 12.09.2026 15:28"


def test_ctf_interval_failure_is_recorded_without_stopping_perfect_flow() -> None:
    source = inspect.getsource(_run_perfect_ctf)
    interval_action = source.index('action="CTF_SANDBOX_PRODUCT_INTERVAL_CONFIRMED"')
    next_action = source.index('action="CTF_TASKS_OPENED"', interval_action)
    interval_block = source[interval_action - 500 : next_action]

    assert "outcomes.append(" in interval_block
    assert "return False" not in interval_block


def test_perfect_ctf_checks_actionbar_before_questions_and_intermediate_materials() -> (
    None
):
    source = inspect.getsource(_run_perfect_ctf)

    stage_opened = source.index('action=f"CTF_STAGE_{stage.stage}_OPENED"')
    actionbar = source.index("ACTIONBAR_SCHEME_CONFIRMED")
    questions = source.index("for question_index, question in enumerate")

    assert stage_opened < actionbar < questions
    assert "if await ctf_page.actionbar_scheme_is_available()" in source
    assert "operation=ctf_page.verify_actionbar_materials" in source
    assert (
        "verify_stage_scheme_dialog(\n                        final_stage=False"
        in source
    )
    assert "ctf_page.verify_stage_video(final_stage=False)" in source
    assert "verify_stage_scheme_dialog(final_stage=final_stage)" not in source

    completion_start = source.index("final_stage = stage_index")
    final_metrics = source.index('action="CTF_FINAL_PERFORMANCE_CONFIRMED"')
    completion_flow = source[completion_start:final_metrics]
    assert completion_flow.index("if not final_stage:") < completion_flow.index(
        "SCHEME_DIALOG_CONFIRMED"
    )
    assert completion_flow.index("SCHEME_DIALOG_CONFIRMED") < completion_flow.index(
        "VIDEO_CONFIRMED"
    )


def test_ctf_actionbar_uses_exact_sandbox_scheme_dom_contract() -> None:
    availability_source = inspect.getsource(
        CtfScenarioPage.actionbar_scheme_is_available
    )
    materials_source = inspect.getsource(CtfScenarioPage.verify_actionbar_materials)
    open_source = inspect.getsource(
        CtfScenarioPage._ensure_actionbar_open_from_inline_scheme
    )
    inline_source = inspect.getsource(CtfScenarioPage.verify_actionbar_inline_scheme)
    inline_locator_source = inspect.getsource(
        CtfScenarioPage._actionbar_inline_scheme_image
    )
    right_container_source = inspect.getsource(
        CtfScenarioPage._actionbar_right_container
    )
    dialog_source = inspect.getsource(CtfScenarioPage._verify_actionbar_scheme_dialog)

    assert ".scenarios-result__right-container:visible" in availability_source
    assert ".basic-scenario-actionbar-show:visible" in availability_source
    assert "except Exception" in availability_source
    assert "return False" in availability_source
    assert "verify_actionbar_inline_scheme" in materials_source
    assert "verify_actionbar_scheme_dialog" in materials_source
    assert ".basic-scenario-actionbar-show:visible" in open_source
    assert "_click_with_fallback(expand)" in open_source
    assert "inline_scheme.is_visible" in open_source
    assert "_wait_for_image_loaded(inline_scheme)" in open_source
    assert "matrix-actionbar:visible).first" not in open_source
    assert ".scenarios-result__right-container:visible" in right_container_source
    assert "_actionbar_right_container()" in inline_locator_source
    assert ".scenarios-result__scheme-container" in inline_locator_source
    assert "matrix-actionbar:visible" not in inline_locator_source
    assert r're.compile(r"\S+")' in inline_source
    assert "steps_background" not in inline_source
    assert "steps_background.svg" not in inline_locator_source
    assert r"^\s*Схема атаки\s*$" in dialog_source
    assert "_actionbar_right_container()" in dialog_source
    assert "span.statistics-link.body-3.cursor:visible" in dialog_source
    assert "statistics-link-cursor" not in dialog_source
    assert ".scheme-dialog-container" in dialog_source
    assert "img[src]:visible" in dialog_source
    assert "steps_background.svg" not in dialog_source
    assert "button#reportModalSubmit:visible" in dialog_source


def test_sandbox_ctf_product_interval_uses_one_product_tab() -> None:
    source = inspect.getsource(_run_perfect_ctf)
    full_flow_source = inspect.getsource(_run_full_scenario)

    assert "reopen_after_login=False" in source
    assert "reopen_after_login=False" in full_flow_source


def test_post_ctf_return_recognizes_existing_basic_scenario() -> None:
    return_source = inspect.getsource(
        SandboxTestDriveScenarioPage.return_from_ctf_to_basic_modules
    )
    url_source = inspect.getsource(
        SandboxTestDriveScenarioPage._basic_scenario_url_is_open
    )

    assert "[id^='scenario_step_id_']:visible" in return_source
    assert "self._basic_scenario_url_is_open()" in return_source
    assert "basicUrl || basicReady" in return_source
    assert "прямой переход по URL не требуется" in return_source
    assert "settings.BASIC_CASE_ID" in url_source
    assert 'params.get("scenarioType") == ["basic"]' in url_source

    page = SimpleNamespace(
        url=(
            "http://192.168.91.71/scenarios?product=sandbox&scenario=c-1"
            "&basicStepId=24&scenarioType=basic"
        )
    )
    assert SandboxTestDriveScenarioPage(page)._basic_scenario_url_is_open()

    page.url = (
        "http://192.168.91.71/scenarios?product=sandbox&scenario=c-10&scenarioType=ctf"
    )
    assert not SandboxTestDriveScenarioPage(page)._basic_scenario_url_is_open()


def test_sandbox_stage_verification_does_not_open_or_scroll_to_a_question() -> None:
    flow_source = inspect.getsource(_run_perfect_ctf)
    source = inspect.getsource(
        CtfScenarioPage.verify_stage_opened_without_question_interaction
    )
    task_source = inspect.getsource(CtfScenarioPage.task_card)

    assert "verify_stage_opened_without_question_interaction" in flow_source
    assert "_ensure_actionbar_open" not in source
    assert "click(" not in source
    assert "trial=True" not in source
    assert 'get_by_text(f"Этап {stage_number}"' in source
    assert "for question_index in range(expected_questions)" in source
    assert "self.task_card(question_index)" in source
    assert "to_have_count" not in source
    assert "#matrix_scenarios_task_{question_index}:visible" in task_source
    assert ".scenario-tasks-card:visible" not in task_source
    assert ".nth(question_index)" not in task_source


@pytest.mark.asyncio
async def test_specialized_finish_accepts_server_completed_statuses() -> None:
    scenario_page = SimpleNamespace(ensure_test_drive_finished=AsyncMock())
    modules_page = SimpleNamespace(
        read_module_statuses=AsyncMock(
            return_value={1: "completed", 2: "skipped_by_bs"}
        )
    )
    content = SimpleNamespace(modules=(object(), object()))

    await _ensure_specialized_test_drive_finished(
        scenario_page=scenario_page,
        modules_page=modules_page,
        content=content,
    )

    scenario_page.ensure_test_drive_finished.assert_not_awaited()


@pytest.mark.asyncio
async def test_specialized_finish_clicks_when_statuses_are_not_final() -> None:
    scenario_page = SimpleNamespace(ensure_test_drive_finished=AsyncMock())
    modules_page = SimpleNamespace(
        read_module_statuses=AsyncMock(return_value={1: "completed", 2: "undefined"})
    )
    content = SimpleNamespace(modules=(object(), object()))

    await _ensure_specialized_test_drive_finished(
        scenario_page=scenario_page,
        modules_page=modules_page,
        content=content,
    )

    scenario_page.ensure_test_drive_finished.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_specialized_finish_accepts_status_race_after_disabled_button() -> None:
    scenario_page = SimpleNamespace(
        ensure_test_drive_finished=AsyncMock(
            side_effect=AssertionError("Locator expected to be enabled")
        )
    )
    modules_page = SimpleNamespace(
        read_module_statuses=AsyncMock(
            side_effect=(
                {1: "completed", 2: "undefined"},
                {1: "completed", 2: "skipped_by_bs"},
            )
        )
    )
    content = SimpleNamespace(modules=(object(), object()))

    await _ensure_specialized_test_drive_finished(
        scenario_page=scenario_page,
        modules_page=modules_page,
        content=content,
    )

    scenario_page.ensure_test_drive_finished.assert_awaited_once_with()
    assert modules_page.read_module_statuses.await_count == 2


@pytest.mark.asyncio
async def test_finish_recovery_does_not_reopen_basic_scenario_after_failure() -> None:
    scenario_page = SandboxTestDriveScenarioPage(page=object())
    scenario_page.return_from_ctf_to_basic_modules = AsyncMock()
    scenario_page.finish_test_drive = AsyncMock(
        side_effect=AssertionError("feedback dialog was closed")
    )
    scenario_page.open = AsyncMock()

    with pytest.raises(AssertionError, match="feedback dialog was closed"):
        await scenario_page.ensure_test_drive_finished()

    scenario_page.finish_test_drive.assert_awaited_once_with()
    scenario_page.open.assert_not_awaited()


@pytest.mark.asyncio
async def test_exhausted_final_feedback_closes_dialog_without_error(
    monkeypatch,
) -> None:
    textarea = object()
    dialog = SimpleNamespace(
        locator=Mock(return_value=SimpleNamespace(first=textarea)),
    )
    page = SimpleNamespace(locator=Mock(return_value=SimpleNamespace(last=dialog)))
    scenario_page = SandboxTestDriveScenarioPage(page=page)
    scenario_page._close_final_feedback_dialog = AsyncMock(return_value=True)
    monkeypatch.setattr(settings, "FINAL_FEEDBACK_MAX_ATTEMPTS", 0)
    monkeypatch.setattr(
        sandbox_scenario_page_module,
        "expect",
        lambda target: _FieldExpectation(target),
    )

    status = await scenario_page.submit_final_feedback("feedback")

    assert status is None
    scenario_page._close_final_feedback_dialog.assert_awaited_once_with(dialog)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "leaderboard_expected"),
    (
        ("answers_and_hints", False),
        ("solutions", False),
        ("stage_statuses", False),
        ("question_metrics", True),
    ),
)
async def test_only_question_metrics_opens_leaderboard_after_finish(
    monkeypatch,
    profile: str,
    leaderboard_expected: bool,
) -> None:
    run_critical = AsyncMock(return_value=True)
    run_check = AsyncMock(return_value=True)
    monkeypatch.setattr(sandbox_runner, "_run_critical", run_critical)
    monkeypatch.setattr(sandbox_runner, "_run_check", run_check)
    user = DriveUser(
        id="user",
        username="playwright_user",
        password="secret",
        enabled=True,
        test_profile=profile,
    )

    result = await _finish_specialized_profile_test_drive(
        scenario_page=SimpleNamespace(page=object()),
        modules_page=object(),
        content=SimpleNamespace(total_questions=20),
        ctf_content=SimpleNamespace(total_questions=9),
        user=user,
        collector=object(),
    )

    actions = [call.kwargs["action"] for call in run_check.await_args_list]
    assert result is True
    assert ("LEADERBOARD_RESULTS_CONFIRMED" in actions) is leaderboard_expected


@pytest.mark.asyncio
async def test_basic_cyrillic_password_uses_unicode_insert_text_fallback(
    monkeypatch,
) -> None:
    field = _UnicodeRejectingField("старое")
    modules_page = BasicModulesPage(page=_KeyboardPage(field))
    monkeypatch.setattr(
        basic_modules_page_module,
        "expect",
        lambda target: _FieldExpectation(target),
    )

    await modules_page._replace_text_field_value(field, "Троян")

    assert field.value == "Троян"
    assert "insert_text" in field.calls
    assert "select_text" in field.calls
    assert "fill:" not in field.calls
    assert "native" not in field.calls


@pytest.mark.asyncio
async def test_basic_cyrillic_password_can_use_character_keyboard_input(
    monkeypatch,
) -> None:
    field = _SequentialUnicodeField("старое")
    modules_page = BasicModulesPage(page=_KeyboardPage(field, accepts_text=False))
    monkeypatch.setattr(
        basic_modules_page_module,
        "expect",
        lambda target: _FieldExpectation(target),
    )

    await modules_page._replace_text_field_value(field, "Троян")

    assert field.value == "Троян"
    assert "press_sequentially" in field.calls
    assert "insert_text" in field.calls


@pytest.mark.asyncio
async def test_basic_input_native_fallback_does_not_require_mouse_click(
    monkeypatch,
) -> None:
    field = _ClickBlockedField("старое")
    modules_page = BasicModulesPage(page=_KeyboardPage(field, accepts_text=False))
    monkeypatch.setattr(
        basic_modules_page_module,
        "expect",
        lambda target: _FieldExpectation(target),
    )

    await modules_page._replace_text_field_value(field, "Троян")

    assert field.value == "Троян"
    assert field.calls.count("click_blocked") == 3
    assert "native" in field.calls


def test_basic_hint_flow_is_dom_driven_and_always_restores_the_card() -> None:
    source = inspect.getsource(BasicModulesPage.verify_question_hint)
    runner_source = inspect.getsource(_run_full_scenario)

    assert "button#hint-btn:visible" in source
    assert "if exercise.hint is None:\n            return" not in source
    assert "finally:" in source
    assert "_restore_question_after_hint" in source
    assert 'user.test_profile == "answers_and_hints"' in runner_source
    assert "if False and exercise.hint" not in runner_source


def test_sandbox_rejected_basic_answers_are_not_reported_as_skipped() -> None:
    source = inspect.getsource(_run_full_scenario)

    rejection_block = source.split(
        "except (\n                QuestionContentMismatchError,",
        maxsplit=1,
    )[1].split("except Exception as exc:", maxsplit=1)[0]
    assert "record_fail" in rejection_block
    assert "_SKIPPED" not in rejection_block
    assert "record_skip" not in rejection_block


@pytest.mark.asyncio
async def test_basic_input_is_refilled_when_angular_resets_it_before_submit(
    monkeypatch,
) -> None:
    modules_page = BasicModulesPage(page=object())
    field = _AppendingField("")
    card = _InputCard(field)
    exercise = BasicExercise(
        step_id=7,
        order=3,
        question_type="input",
        question="Какой вердикт?",
        options=(),
        answers=("Троян",),
        hint="Откройте результаты анализа",
    )
    monkeypatch.setattr(
        basic_modules_page_module,
        "expect",
        lambda target: _FieldExpectation(target),
    )

    await modules_page._ensure_input_answer_ready(
        card,
        exercise,
        "Троян",
        attempt="correct#1",
    )

    assert field.value == "Троян"
    assert "fill:Троян" in field.calls


@pytest.mark.asyncio
async def test_unfillable_input_becomes_soft_question_rejection(
    monkeypatch,
) -> None:
    field = _UnicodeRejectingField("")
    field.evaluate = AsyncMock(return_value=None)
    modules_page = BasicModulesPage(page=_KeyboardPage(field, accepts_text=False))
    monkeypatch.setattr(
        basic_modules_page_module,
        "expect",
        lambda target: _FieldExpectation(target),
    )

    with pytest.raises(QuestionAnswerRejectedError, match="submit не выполнен"):
        await modules_page._replace_text_field_value(field, "Троян")


def test_sandbox_pro_http_error_wins_over_dom_timeout() -> None:
    source = inspect.getsource(SandboxTestDriveScenarioPage.submit_pro_request)
    error_source = inspect.getsource(
        SandboxTestDriveScenarioPage._describe_pro_http_error
    )

    assert "asyncio.FIRST_COMPLETED" in source
    assert "response_received" in source
    assert "not response.ok" in source
    assert "ProRequestHttpError" in source
    assert "await self._describe_pro_http_error(response)" in source
    assert "response.status" in error_source
    assert "await response.text()" in error_source


@pytest.mark.asyncio
async def test_perfect_ctf_metrics_use_completed_card_dom() -> None:
    events: list[str] = []
    completed_metrics = QuestionMetricsExpectation(
        attempt_count=1,
        accuracy_percent=100,
        duration_seconds=60,
        actual_seconds=8,
        speed_percent=750,
        base_points=10,
        speed_bonus_points=2,
        awarded_points=12,
    )

    async def submit_answer(_task) -> None:
        events.append("submit")

    async def read_completed_metrics(**_kwargs):
        events.append("completed-metrics")
        return completed_metrics

    ctf_page = SimpleNamespace(
        open_question=AsyncMock(return_value=object()),
        read_task_duration_seconds=AsyncMock(return_value=60),
        fill_answer=AsyncMock(),
        read_submission_elapsed_seconds=AsyncMock(return_value=1),
        submit_answer=AsyncMock(side_effect=submit_answer),
        verify_answer_accepted=AsyncMock(),
        verify_completed_question_metrics=AsyncMock(side_effect=read_completed_metrics),
        collapse_question=AsyncMock(),
    )
    question = CtfQuestion(
        stage=1,
        local_order=1,
        global_order=1,
        answer="flag",
        points=10,
        duration_seconds=60,
    )

    result = await _answer_ctf_question(
        ctf_page=ctf_page,
        question=question,
        last_in_stage=False,
        final_question=False,
    )

    assert result == completed_metrics
    assert events == ["submit", "completed-metrics"]


@pytest.mark.asyncio
async def test_module_without_copyable_filters_creates_no_false_skip() -> None:
    modules_page = SimpleNamespace(
        verify_copyable_filters=AsyncMock(return_value=()),
    )
    collector = SimpleNamespace(
        record_skip=Mock(),
        record_pass=Mock(),
    )
    user = DriveUser(
        id="user-1",
        username="playwright_user_1",
        password="secret",
        enabled=True,
        test_profile="perfect",
    )
    module = BasicModule(
        step_id=3,
        title="О PT Sandbox",
        module_type="ex",
        content_filename="11538304011_ru.html",
        ancestor_section_ids=(1,),
        ancestor_section_titles=("Введение",),
        required_for_ctf=False,
        exercises=(),
    )

    result = await _run_copyable_filters_check(
        modules_page=modules_page,
        collector=collector,
        user=user,
        page=object(),
        module=module,
    )

    assert result is True
    collector.record_skip.assert_not_called()
    collector.record_pass.assert_not_called()


def test_pro_request_failures_do_not_block_final_finish_flow() -> None:
    intro_source = inspect.getsource(_run_shared_introduction)
    flow_source = inspect.getsource(_run_full_scenario)

    intro_action = intro_source.index('action="PRO_VERSION_REQUEST_SUBMITTED"')
    assert (
        "await _run_optional_skip(" in intro_source[intro_action - 300 : intro_action]
    )

    complex_action = flow_source.index(
        'action="COMPLEX_SCENARIO_PRO_REQUEST_SUBMITTED"'
    )
    finish_action = flow_source.index(
        "operation=scenario_page.open_final_feedback_from_finish_button"
    )
    assert (
        "await _run_optional_skip("
        in flow_source[complex_action - 300 : complex_action]
    )
    assert complex_action < finish_action
    assert "return False" not in flow_source[complex_action:finish_action]
