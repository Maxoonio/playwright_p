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

import siem_test_drive_scenario_test
from exceptions import ScenarioChecksErrored


@pytest.mark.asyncio
async def test_siem_c2_reports_execution_errors_to_pytest(
    monkeypatch,
) -> None:
    async def fake_run_siem_c2(*, users_path):
        raise ScenarioChecksErrored("Scenario checks failed: 0 FAILED, 1 ERROR.")

    monkeypatch.setattr(
        siem_test_drive_scenario_test,
        "run_siem_c2",
        fake_run_siem_c2,
    )

    with pytest.raises(ScenarioChecksErrored):
        await siem_test_drive_scenario_test.test_siem_c2()
