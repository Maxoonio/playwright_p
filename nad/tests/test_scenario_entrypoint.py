import sys
from pathlib import Path

import pytest

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_ROOT = TEST_DRIVE_ROOT / "scenarios"
for import_path in (
    TEST_DRIVE_ROOT / "common",
    SCENARIOS_ROOT,
    TEST_DRIVE_ROOT,
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import test_drive_scenario_test
from exceptions import ScenarioChecksErrored


@pytest.mark.asyncio
async def test_nad_c3_reports_execution_errors_to_pytest(
    monkeypatch,
) -> None:
    async def fake_run_nad_c3(*, users_path):
        raise ScenarioChecksErrored("Scenario checks failed: 0 FAILED, 1 ERROR.")

    monkeypatch.setattr(
        test_drive_scenario_test,
        "run_nad_c3",
        fake_run_nad_c3,
    )

    with pytest.raises(ScenarioChecksErrored):
        await test_drive_scenario_test.test_nad_c3()
