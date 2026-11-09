import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

BASE_URL = "http://192.168.91.71"
LOGIN_URL = f"{BASE_URL}/login"
PRODUCT_SLUG = "siem"
PRODUCT_NAME = "MaxPatrol SIEM"
BASIC_CASE_ID = "c-2"
CTF_CASE_ID = "c-20"
SCENARIO_LABEL = "MaxPatrol SIEM test-drive c-2"
SIEM_C2_SCENARIO_URL = (
    f"{BASE_URL}/scenarios?product=siem&scenario=c-2&basicStepId=1&scenarioType=basic"
)
SCENARIO_URL = SIEM_C2_SCENARIO_URL
PRODUCT_HOST = "192.168.91.188"
PRODUCT_INTERVAL_KIND = "siem"
PRODUCT_LOGIN = os.getenv("PT_TEST_DRIVE_PRODUCT_LOGIN", "matrixadm").strip()
PRODUCT_PASSWORD = os.getenv("PT_TEST_DRIVE_PRODUCT_PASSWORD", "P@ssw0rd")
SCENARIO_PAGE_MODULE = "pages.siem_scenario_page"
SCENARIO_PAGE_CLASS = "SiemTestDriveScenarioPage"
PRODUCT_ABOUT_STEP_ID = 3
PRODUCT_ABOUT_TITLE = "О MaxPatrol SIEM"
NEXT_AFTER_PRODUCT_ABOUT_STEP_ID = 4
NEXT_AFTER_PRODUCT_ABOUT_TITLE = "Инфраструктура стенда"
CTF_SECTION_ID = 4
CTF_SECTION_TITLE = "Поиск флага. Риск кражи денег в организации"
COMPLEX_SECTION_ID = 5
COMPLEX_STEP_ID = 161

# Контент test-drive загружается перед запуском напрямую из GitLab.
CONTENT_GITLAB_BASE_URL = os.getenv(
    "PT_TRAINER_CONTENT_GITLAB_URL",
    "https://gitlab.ptsecurity.com",
).rstrip("/")
CONTENT_GITLAB_PROJECT = os.getenv(
    "PT_TRAINER_CONTENT_PROJECT",
    "sbd/pt-trainer-content",
).strip()
CONTENT_GITLAB_REF = (
    os.getenv("PT_TRAINER_CONTENT_REF", "testdrive").strip() or "testdrive"
)
CTF_CONTENT_GITLAB_REF = (
    os.getenv("PT_TRAINER_CTF_CONTENT_REF", "testdrive").strip() or "testdrive"
)
CONTENT_GITLAB_TOKEN_ENV = "PT_TRAINER_CONTENT_TOKEN"
CONTENT_GITLAB_REQUEST_TIMEOUT_SECONDS = 20
CONTENT_GIT_CLONE_TIMEOUT_SECONDS = 45
CONTENT_GIT_REPOSITORY_URL = os.getenv(
    "PT_TRAINER_CONTENT_GIT_URL",
    (f"{CONTENT_GITLAB_BASE_URL}/{CONTENT_GITLAB_PROJECT}.git"),
).strip()


def _content_cache_dir() -> Path:
    configured = os.getenv("PT_TEST_DRIVE_CONTENT_CACHE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()

    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        return (
            Path(local_app_data) / "PTMatrixPlaywright" / "test-drive" / "content-cache"
        )

    return (
        Path.home() / ".cache" / "pt-matrix-playwright" / "test-drive" / "content-cache"
    )


CONTENT_CACHE_DIR = _content_cache_dir()
CONTENT_FORCE_REFRESH = os.getenv(
    "PT_TEST_DRIVE_REFRESH_CONTENT",
    "true",
).casefold() in {
    "1",
    "true",
    "yes",
}

DEFAULT_TIMEOUT_MS = 20_000
NETWORK_TIMEOUT_MS = 15_000
DOM_TIMEOUT_MS = 15_000
APP_START_TIMEOUT_MS = 120_000
PRODUCT_LOAD_TIMEOUT_MS = 90_000
CTF_TRANSITION_TIMEOUT_MS = 10_000
CTF_ANSWER_STATE_TIMEOUT_MS = 10_000
CTF_MATERIAL_TIMEOUT_MS = 10_000
CTF_METRICS_TIMEOUT_MS = 10_000
COMPLEX_SCENARIO_COMPLETION_DWELL_MS = 5_000
THEORY_MODULE_COMPLETION_DWELL_MS = 5_000
BASIC_QUESTION_STATUS_TIMEOUT_MS = 45_000
BASIC_CORRECT_ANSWER_MAX_ATTEMPTS = 3
BASIC_CORRECT_ANSWER_RETRY_TIMEOUT_MS = 5_000
LOGIN_FORM_HIDE_TIMEOUT_MS = 60_000
LOGIN_MAX_ATTEMPTS = 3

SOS_DESCRIPTION = "Тестовое сообщение Playwright"
PRO_COMPANY_NAME = "test"
PRO_COMPANY_TIN = "2222222222"
PRO_REQUEST_MAX_ATTEMPTS = 2
FINAL_FEEDBACK_TEXT = "Автоматизированный тестовый отзыв Playwright"
FINAL_FEEDBACK_MAX_ATTEMPTS = 2
FINISH_PROGRESS_THRESHOLD = 5
SPECIALIZED_CTF_ENTRY_PROGRESS_PERCENT = 70
QUESTION_METRICS_STATE_FILE = (
    PROJECT_ROOT / "artifacts" / ".question-metrics-state.json"
)
ALLURE_RESULTS_DIR = PROJECT_ROOT / "allure-results"
ALLURE_RESULTS_CLEANUP_INTERVAL = 3
ALLURE_CLEANUP_STATE_FILE = PROJECT_ROOT / "artifacts" / ".allure-run-count"
SCREENSHOTS_CLEANUP_INTERVAL = ALLURE_RESULTS_CLEANUP_INTERVAL
SCREENSHOTS_CLEANUP_STATE_FILE = PROJECT_ROOT / "artifacts" / ".screenshots-run-count"

VIEWPORT_WIDTH = 1920
VIEWPORT_HEIGHT = 1080
HEADLESS = False
SLOW_MO_MS = 0
LOCALE = "ru-RU"
MAX_PARALLEL_BROWSERS = 3

FAILURE_SCREENSHOTS_DIR = PROJECT_ROOT / "artifacts" / "screenshots"
