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
from pages.basic_modules_page import (
    BasicModulesPage,
    QuestionAnswerResult,
)
from scenarios.runner import (
    _expected_module_status_before,
    _expected_progress_percent,
    _finish_specialized_profile_test_drive,
    _run_full_scenario,
    _verify_early_finish_statuses,
    _verify_stage_status_profile_final_modules,
)


@pytest.mark.asyncio
async def test_allowed_module_status_waits_for_dom_transition(
    monkeypatch,
) -> None:
    driver = BasicModulesPage(object())  # type: ignore[arg-type]
    module = BasicModule(
        step_id=24,
        title="Поиск DNS-запросов к DGA-доменам",
        module_type="ex",
        content_filename=None,
        ancestor_section_ids=(),
        ancestor_section_titles=(),
        required_for_ctf=False,
        exercises=(),
    )
    root = object()
    calls: list[tuple[object, set[str]]] = []

    async def ensure_visible(_: BasicModule) -> None:
        return None

    async def wait_for_allowed_status(
        actual_root: object,
        allowed_statuses: set[str],
    ) -> str:
        calls.append((actual_root, allowed_statuses))
        return "completed"

    monkeypatch.setattr(
        driver,
        "_ensure_module_visible",
        ensure_visible,
    )
    monkeypatch.setattr(
        driver,
        "module_step",
        lambda _: root,
    )
    monkeypatch.setattr(
        driver,
        "_wait_for_allowed_status",
        wait_for_allowed_status,
    )

    actual = await driver.verify_module_status_allowed(
        module,
        {"completed"},
    )

    assert actual == "completed"
    assert calls == [(root, {"completed"})]


def test_allowed_status_wait_is_driven_by_dom_mutations() -> None:
    source = inspect.getsource(BasicModulesPage._wait_for_allowed_status)

    assert "MutationObserver" in source
    assert "allowed.includes" in source


def test_all_basic_module_statuses_are_supported() -> None:
    assert BasicModulesPage._KNOWN_STATUSES == {
        "undefined",
        "started",
        "partial_success",
        "completed",
        "skipped_by_bs",
    }


def test_progress_is_calculated_only_from_completed_modules() -> None:
    statuses = {
        1: "completed",
        2: "completed",
        3: "partial_success",
        4: "started",
        5: "skipped_by_bs",
        6: "undefined",
    }

    assert _expected_progress_percent(statuses) == pytest.approx(100 / 3)


def test_partial_module_status_is_not_disabled() -> None:
    source = inspect.getsource(_verify_early_finish_statuses)

    assert "elif module.step_id == 3" in source
    assert "False and module.step_id == 3" not in source
    assert '"partial_success"' in source
    assert "FINAL_SKIPPED_BY_BS_CONFIRMED" in source


def test_intermediate_module_status_is_checked_exactly() -> None:
    source = inspect.getsource(_run_full_scenario)

    assert "expected_during_status" in source
    assert "verify_module_status_expected" in source
    assert "MODULE_{module.step_id}_PROGRESS_" in source
    assert "verify_incorrect_first = not module_status_profile" in source


@pytest.mark.parametrize(
    ("status_before", "expected"),
    (
        ("undefined", "undefined"),
        ("partial_success", "undefined"),
        ("started", "started"),
        ("completed", "completed"),
    ),
)
def test_expected_status_before_module(
    status_before: str,
    expected: str,
) -> None:
    assert _expected_module_status_before(status_before) == expected


def test_infrastructure_status_mismatch_does_not_abort_flow() -> None:
    source = inspect.getsource(_run_full_scenario)

    assert "Любой новый SIEM-модуль" in source
    assert "_expected_module_status_before(status_before)" in source
    assert "status_before_confirmed = await _run_check(" in source
    assert "module.step_id != settings.NEXT_AFTER_PRODUCT_ABOUT_STEP_ID" in source


def test_fourth_profile_owns_basic_module_status_lifecycle() -> None:
    flow_source = inspect.getsource(_run_full_scenario)
    finish_source = inspect.getsource(_finish_specialized_profile_test_drive)
    final_source = inspect.getsource(_verify_stage_status_profile_final_modules)

    assert "module_status_profile = _is_module_status_profile(user)" in (flow_source)
    assert "check_module_statuses = perfect_profile or module_status_profile" in (
        flow_source
    )
    assert "STATUS_BEFORE_CONFIRMED" in flow_source
    assert "STATUS_DURING_CONFIRMED" in flow_source
    assert "PARTIAL_SUCCESS_CONFIRMED" not in flow_source
    assert "submit_incorrect_answer_only" not in flow_source
    assert "verify_incorrect_first = not module_status_profile" in flow_source
    assert '"partial_success"' in flow_source
    assert "STATUS_AFTER_NEXT_CONFIRMED" in flow_source
    assert "STATUS_AT_CTF_THRESHOLD_CONFIRMED" not in flow_source
    assert "if _is_module_status_profile(user):" in finish_source
    assert "BASIC_MODULE_FINAL_STATUSES_CONFIRMED" in finish_source
    assert '{"completed", "skipped_by_bs"}' in final_source
    assert "if not completed" in final_source
    assert "if not skipped" in final_source


def test_fourth_profile_does_not_force_partial_success_before_correct_answer() -> None:
    flow_source = inspect.getsource(_run_full_scenario)
    incorrect_source = inspect.getsource(BasicModulesPage.submit_incorrect_answer_only)

    assert "submit_incorrect_answer_only" not in flow_source
    assert "PARTIAL_SUCCESS_CONFIRMED" not in flow_source
    assert "verify_incorrect_first = not module_status_profile" in flow_source
    assert "_submit_incorrect_answer" in incorrect_source
    assert "_submit_correct_answer_with_retries" not in incorrect_source


@pytest.mark.asyncio
async def test_fourth_profile_final_statuses_require_completed_and_skipped() -> None:
    modules = tuple(
        BasicModule(
            step_id=step_id,
            title=f"Module {step_id}",
            module_type="ex",
            content_filename=None,
            ancestor_section_ids=(),
            ancestor_section_titles=(),
            required_for_ctf=False,
            exercises=(),
        )
        for step_id in (1, 2)
    )

    class ModulesPageStub:
        async def verify_module_status_allowed(
            self,
            module: BasicModule,
            allowed: set[str],
        ) -> str:
            assert allowed == {"completed", "skipped_by_bs"}
            return {
                1: "completed",
                2: "skipped_by_bs",
            }[module.step_id]

    class ContentStub:
        def __init__(self) -> None:
            self.modules = modules

    await _verify_stage_status_profile_final_modules(
        modules_page=ModulesPageStub(),  # type: ignore[arg-type]
        content=ContentStub(),  # type: ignore[arg-type]
    )


def test_question_answer_result_exposes_failed_and_completed_flow() -> None:
    result = QuestionAnswerResult(
        already_completed=False,
        failed_status_confirmed=True,
    )

    assert result.failed_status_confirmed is True
    assert result.already_completed is False
