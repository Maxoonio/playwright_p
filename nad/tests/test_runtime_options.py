import ast
from pathlib import Path
from urllib.parse import urlsplit

import settings

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = TEST_DRIVE_ROOT.parent / "common"
PROJECT_ROOT = TEST_DRIVE_ROOT.parent


def test_runtime_outputs_are_scoped_to_nad() -> None:
    assert settings.PROJECT_ROOT == TEST_DRIVE_ROOT
    assert settings.ALLURE_RESULTS_DIR == (TEST_DRIVE_ROOT / "allure-results")
    assert settings.FAILURE_SCREENSHOTS_DIR == (
        TEST_DRIVE_ROOT / "artifacts" / "screenshots"
    )
    assert settings.QUESTION_METRICS_STATE_FILE.parent == (
        TEST_DRIVE_ROOT / "artifacts"
    )


def test_headless_cli_and_allure_dependency_are_declared() -> None:
    pytest_support_source = (SHARED_ROOT / "pytest_support.py").read_text(
        encoding="utf-8"
    )
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert '"--headless"' in pytest_support_source
    assert '"--headed"' in pytest_support_source
    assert "PT_TEST_DRIVE_HEADLESS" in pytest_support_source
    assert "environment.properties" in pytest_support_source
    assert "allure-pytest" in requirements


def test_runtime_artifacts_are_cleaned_by_pytest_session() -> None:
    pytest_support_source = (SHARED_ROOT / "pytest_support.py").read_text(
        encoding="utf-8"
    )
    settings_source = (TEST_DRIVE_ROOT / "common" / "settings.py").read_text(
        encoding="utf-8"
    )

    assert "prepare_periodic_cleanup(" in pytest_support_source
    assert "settings.FAILURE_SCREENSHOTS_DIR" in pytest_support_source
    assert "SCREENSHOTS_CLEANUP_INTERVAL" in pytest_support_source
    assert "SCREENSHOTS_CLEANUP_STATE_FILE" in pytest_support_source
    assert "_prepare_periodic_allure_cleanup(report_path)" in pytest_support_source
    assert "ALLURE_RESULTS_CLEANUP_INTERVAL" in settings_source
    assert "ALLURE_CLEANUP_STATE_FILE" in settings_source


def test_regular_runtime_settings_are_plain_constants() -> None:
    settings_source = (TEST_DRIVE_ROOT / "common" / "settings.py").read_text(
        encoding="utf-8"
    )

    base_url = next(
        node.value
        for node in ast.parse(settings_source).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "BASE_URL"
            for target in node.targets
        )
    )
    assert isinstance(base_url, ast.Constant)
    assert isinstance(base_url.value, str)
    parsed_base_url = urlsplit(base_url.value)
    assert parsed_base_url.scheme in {"http", "https"}
    assert parsed_base_url.hostname
    assert not parsed_base_url.query
    assert settings.LOGIN_URL == f"{settings.BASE_URL}/login"
    assert (
        "product=nad&scenario=c-3&basicStepId=1&scenarioType=basic" in settings_source
    )
    assert "APP_START_TIMEOUT_MS = 120_000" in settings_source
    assert "PRODUCT_LOAD_TIMEOUT_MS = 90_000" in settings_source
    assert "PRODUCT_READINESS_TIMEOUT_MS = 30_000" in settings_source
    assert "PRODUCT_FIRST_LOGIN_SETTLE_MS = 5_000" in settings_source
    assert "BASIC_QUESTION_STATUS_TIMEOUT_MS = 45_000" in settings_source
    assert "LOGIN_FORM_HIDE_TIMEOUT_MS = 60_000" in settings_source
    assert 'SOS_DESCRIPTION = "Тестовое сообщение Playwright"' in settings_source
    assert 'PRO_COMPANY_NAME = "test"' in settings_source
    assert 'PRO_COMPANY_TIN = "2222222222"' in settings_source
    assert "ALLURE_RESULTS_CLEANUP_INTERVAL = 3" in settings_source
    assert "HEADLESS = False" in settings_source
    assert "SLOW_MO_MS = 0" in settings_source
    assert "MAX_PARALLEL_BROWSERS = 3" in settings_source

    for env_name in (
        "PT_TEST_DRIVE_BASE_URL",
        "PT_TEST_DRIVE_APP_START_TIMEOUT_MS",
        "PT_TEST_DRIVE_BASIC_QUESTION_STATUS_TIMEOUT_MS",
        "PT_TEST_DRIVE_LOGIN_FORM_HIDE_TIMEOUT_MS",
        "PT_TEST_DRIVE_SOS_MESSAGE",
        "PT_TEST_DRIVE_PRO_COMPANY",
        "PT_TEST_DRIVE_PRO_TIN",
        "PT_TEST_DRIVE_FEEDBACK_TEXT",
        "PT_TEST_DRIVE_ALLURE_CLEANUP_INTERVAL",
        "PT_TEST_DRIVE_SLOW_MO_MS",
        "PT_TEST_DRIVE_MAX_PARALLEL_BROWSERS",
    ):
        assert env_name not in settings_source


def test_browser_disables_background_throttling() -> None:
    browser_source = (SHARED_ROOT / "utils" / "browser.py").read_text(encoding="utf-8")

    assert "--disable-background-timer-throttling" in browser_source
    assert "--disable-backgrounding-occluded-windows" in browser_source
    assert "--disable-renderer-backgrounding" in browser_source
    assert "CalculateNativeWinOcclusion" in browser_source
    assert "IntensiveWakeUpThrottling" in browser_source
    assert '"args": _browser_args()' in browser_source
