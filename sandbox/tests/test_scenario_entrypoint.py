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

import sandbox_test_drive_scenario_test
from exceptions import ScenarioChecksErrored


@pytest.mark.asyncio
async def test_sandbox_c1_reports_execution_errors_to_pytest(monkeypatch) -> None:
    async def fake_run_sandbox_c1(*, users_path):
        raise ScenarioChecksErrored("Scenario checks failed: 0 FAILED, 1 ERROR.")

    monkeypatch.setattr(
        sandbox_test_drive_scenario_test,
        "run_sandbox_c1",
        fake_run_sandbox_c1,
    )

    with pytest.raises(ScenarioChecksErrored):
        await sandbox_test_drive_scenario_test.test_sandbox_c1()
