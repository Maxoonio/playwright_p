import sys
from pathlib import Path

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
for import_path in (
    TEST_DRIVE_ROOT / "common",
    TEST_DRIVE_ROOT,
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from allure_reporter import (
    _aggregate_message,
    _aggregate_status,
    _group_results,
    publish_allure_results,
)
from models import CheckResult, CheckStatus
from results import ResultCollector


def _result(
    status: CheckStatus,
    *,
    section: int | None = 1,
    step: int | None = 3,
) -> CheckResult:
    return CheckResult(
        username="playwright_user_1",
        profile="perfect",
        section=section,
        step=step,
        action=f"CHECK_{status.value.upper()}",
        status=status,
        message=status.value,
    )


def test_allure_aggregate_preserves_failure_severity() -> None:
    assert (
        _aggregate_status([_result(CheckStatus.PASSED), _result(CheckStatus.ERROR)])
        == CheckStatus.ERROR
    )
    assert (
        _aggregate_status([_result(CheckStatus.PASSED), _result(CheckStatus.FAILED)])
        == CheckStatus.FAILED
    )
    assert _aggregate_status([_result(CheckStatus.SKIPPED)]) == CheckStatus.SKIPPED


def test_allure_groups_checks_by_user_section_and_step() -> None:
    first = _result(CheckStatus.PASSED)
    second = _result(
        CheckStatus.SKIPPED,
        section=None,
        step=None,
    )

    grouped = _group_results([first, second])

    user = grouped[("playwright_user_1", "perfect")]
    assert user[1][3] == [first]
    assert user[None][None] == [second]
    assert "PASSED: 1" in _aggregate_message([first, second])
    assert "SKIPPED: 1" in _aggregate_message([first, second])


def test_result_collector_renders_summary_for_allure() -> None:
    collector = ResultCollector(scenario_label="MaxPatrol SIEM test-drive c-2")
    collector.record_pass(
        username="playwright_user_1",
        profile="perfect",
        action="PROFILE_COMPLETED",
        message="ok",
    )

    summary = collector.render_text_summary()

    assert "MaxPatrol SIEM test-drive c-2" in summary
    assert "Profiles completed:  1" in summary
    assert collector.scenario_label == "MaxPatrol SIEM test-drive c-2"

    # При запуске с --alluredir этот вызов создает вложения и
    # иерархию шагов; без Allure-адаптера он остается безопасным no-op.
    publish_allure_results(collector=collector)
