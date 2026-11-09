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
    _expected_progress_percent,
    _run_full_scenario,
    _verify_early_finish_statuses,
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
    assert "_FAILED_STATUS_CONFIRMED" in source


def test_question_answer_result_exposes_failed_and_completed_flow() -> None:
    result = QuestionAnswerResult(
        already_completed=False,
        failed_status_confirmed=True,
    )

    assert result.failed_status_confirmed is True
    assert result.already_completed is False
