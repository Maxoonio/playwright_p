from pathlib import Path

from data_loader import load_test_drive_users
from models import PERFECT_PROFILE_NAMES, SPECIALIZED_CTF_PROFILE_NAMES

SANDBOX_ROOT = Path(__file__).resolve().parents[1]
USERS_FILE = SANDBOX_ROOT / "data" / "users.local.json"


def test_sandbox_local_users_keep_the_five_profile_ownership_contract() -> None:
    users, errors = load_test_drive_users(USERS_FILE)

    assert errors == []
    assert [(user.username, user.test_profile) for user in users] == [
        ("playwright_user_1", "perfect"),
        ("playwright_user_2", "answers_and_hints"),
        ("playwright_user_3", "solutions"),
        ("playwright_user_4", "stage_statuses"),
        ("playwright_user_5", "question_metrics"),
    ]
    assert len({user.username for user in users}) == 5
    assert PERFECT_PROFILE_NAMES == {"perfect", "full_scenario"}
    assert SPECIALIZED_CTF_PROFILE_NAMES == {
        "answers_and_hints",
        "solutions",
        "stage_statuses",
        "question_metrics",
    }
