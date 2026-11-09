import sys
from pathlib import Path

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
for import_path in (
    TEST_DRIVE_ROOT / "common",
    TEST_DRIVE_ROOT,
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from models import CheckResult, CheckStatus
from results import ResultCollector


def test_result_line_uses_test_drive_section_and_step_labels() -> None:
    result = CheckResult(
        username="playwright_user_1",
        profile="introduction",
        section=1,
        step=2,
        action="MATRIX_VIDEO_TOUR_CONFIRMED",
        status=CheckStatus.PASSED,
        message="Проверено",
    )

    assert ResultCollector.format_result(result) == (
        "PASSED | playwright_user_1 | introduction | "
        "SECTION 1 | STEP 2 | MATRIX_VIDEO_TOUR_CONFIRMED | Проверено"
    )


def test_skipped_result_can_store_screenshot() -> None:
    collector = ResultCollector(scenario_label="SIEM")

    collector.record_skip(
        username="playwright_user_2",
        profile="answers_and_hints",
        action="STEP_1_Q1_SKIPPED",
        message="Skipped with screenshot",
        section=1,
        step=1,
        screenshot="C:/tmp/skip.png",
    )

    result = collector.results[-1]
    assert result.status is CheckStatus.SKIPPED
    assert result.screenshot == "C:/tmp/skip.png"
