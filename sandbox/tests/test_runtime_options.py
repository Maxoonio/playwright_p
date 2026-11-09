from pathlib import Path

import settings

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = TEST_DRIVE_ROOT.parent / "common"
PROJECT_ROOT = TEST_DRIVE_ROOT.parent


def test_sandbox_runtime_contract() -> None:
    assert settings.PROJECT_ROOT == TEST_DRIVE_ROOT
    assert settings.PRODUCT_SLUG == "sandbox"
    assert settings.PRODUCT_NAME == "PT Sandbox"
    assert settings.BASIC_CASE_ID == "c-1"
    assert settings.CTF_CASE_ID == "c-10"
    assert settings.BASE_URL == "http://192.168.91.71"
    assert settings.PRODUCT_INTERVAL_KIND == "sandbox"
    assert settings.PRODUCT_HOST == "192.168.91.14"
    assert settings.COMPLEX_STEP_ID == 161
    assert settings.SPECIALIZED_CTF_ENTRY_PROGRESS_PERCENT == 70
    assert (
        "product=sandbox&scenario=c-1&basicStepId=1&scenarioType=basic"
        in settings.SCENARIO_URL
    )


def test_sandbox_runtime_outputs_are_product_scoped() -> None:
    assert settings.ALLURE_RESULTS_DIR == TEST_DRIVE_ROOT / "allure-results"
    assert settings.FAILURE_SCREENSHOTS_DIR == (
        TEST_DRIVE_ROOT / "artifacts" / "screenshots"
    )
    assert settings.QUESTION_METRICS_STATE_FILE.parent == (
        TEST_DRIVE_ROOT / "artifacts"
    )


def test_headless_allure_and_parallel_browser_options_are_shared() -> None:
    pytest_support_source = (SHARED_ROOT / "pytest_support.py").read_text(
        encoding="utf-8"
    )
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert '"--headless"' in pytest_support_source
    assert '"--headed"' in pytest_support_source
    assert "environment.properties" in pytest_support_source
    assert "_is_live_scenario_session(session.config)" in pytest_support_source
    assert "Unit tests import the product conftest too" in pytest_support_source
    assert "allure-pytest" in requirements
    assert settings.MAX_PARALLEL_BROWSERS == 3
