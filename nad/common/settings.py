import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

BASE_URL = "http://10.10.202.60"
LOGIN_URL = f"{BASE_URL}/login"
PRODUCT_SLUG = "nad"
PRODUCT_NAME = "PT NAD"
BASIC_CASE_ID = "c-3"
CTF_CASE_ID = "c-1"
SCENARIO_LABEL = "PT NAD test-drive c-3"
NAD_C3_SCENARIO_URL = (
    f"{BASE_URL}/scenarios?product=nad&scenario=c-3&basicStepId=1&scenarioType=basic"
)
SCENARIO_URL = NAD_C3_SCENARIO_URL
# Переход выполняется по актуальной ссылке PT NAD из Matrix.
PRODUCT_HOST = ""
PRODUCT_INTERVAL_KIND = "nad"
CTF_SECTION_ID = 5
SCENARIO_PAGE_MODULE = "pages.nad_scenario_page"
SCENARIO_PAGE_CLASS = "NadTestDriveScenarioPage"

CONTENT_GITLAB_BASE_URL = os.getenv(
    "PT_TRAINER_CONTENT_GITLAB_URL",
    "https://gitlab.ptsecurity.com",
).rstrip("/")
CONTENT_GITLAB_PROJECT = os.getenv(
    "PT_TRAINER_CONTENT_PROJECT",
    "sbd/pt-trainer-content",
).strip()
CONTENT_GITLAB_REF = (
    os.getenv("PT_TRAINER_CONTENT_REF", "dev_trainer").strip() or "dev_trainer"
)
CTF_CONTENT_GITLAB_REF = (
    os.getenv("PT_TRAINER_CTF_CONTENT_REF", "test").strip() or "test"
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
    "false",
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
PRODUCT_READINESS_TIMEOUT_MS = 30_000
PRODUCT_FIRST_LOGIN_SETTLE_MS = 5_000
CTF_TRANSITION_TIMEOUT_MS = 10_000
CTF_ANSWER_STATE_TIMEOUT_MS = 10_000
CTF_MATERIAL_TIMEOUT_MS = 10_000
CTF_METRICS_TIMEOUT_MS = 10_000
COMPLEX_SCENARIO_COMPLETION_DWELL_MS = 5_000
THEORY_MODULE_COMPLETION_DWELL_MS = 5_000
BASIC_QUESTION_STATUS_TIMEOUT_MS = 45_000
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

VIEWPORT_WIDTH = 1920
VIEWPORT_HEIGHT = 1080
HEADLESS = False
SLOW_MO_MS = 0
LOCALE = "ru-RU"
MAX_PARALLEL_BROWSERS = 3

FAILURE_SCREENSHOTS_DIR = PROJECT_ROOT / "artifacts" / "screenshots"
