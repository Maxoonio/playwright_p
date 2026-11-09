import json
import sys
from pathlib import Path

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
for import_path in (
    TEST_DRIVE_ROOT / "common",
    TEST_DRIVE_ROOT,
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from data_loader import load_test_drive_users


def test_load_test_drive_users_without_administrator(tmp_path) -> None:
    users_file = tmp_path / "users.local.json"
    users_file.write_text(
        json.dumps(
            {
                "users": [
                    {
                        "id": "intro",
                        "username": "playwright_user_1",
                        "password": "P@ssw0rd",
                        "enabled": True,
                        "test_profile": "introduction",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    users, errors = load_test_drive_users(users_file)

    assert errors == []
    assert len(users) == 1
    assert users[0].username == "playwright_user_1"
    assert users[0].test_profile == "introduction"


def test_unknown_profile_is_reported(tmp_path) -> None:
    users_file = tmp_path / "users.local.json"
    users_file.write_text(
        json.dumps(
            {
                "users": [
                    {
                        "id": "unknown",
                        "username": "user",
                        "password": "secret",
                        "enabled": True,
                        "test_profile": "administrator",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    users, errors = load_test_drive_users(users_file)

    assert users == []
    assert errors[0]["profile"] == "administrator"


def test_requested_five_user_profiles_are_supported(tmp_path) -> None:
    users_file = tmp_path / "users.local.json"
    users_file.write_text(
        json.dumps(
            {
                "users": [
                    {
                        "id": "perfect",
                        "username": "playwright_user_1",
                        "password": "P@ssw0rd",
                        "enabled": True,
                        "test_profile": "perfect",
                    },
                    {
                        "id": "answers_hints",
                        "username": "playwright_user_2",
                        "password": "P@ssw0rd",
                        "enabled": True,
                        "test_profile": "answers_and_hints",
                    },
                    {
                        "id": "solutions",
                        "username": "playwright_user_3",
                        "password": "P@ssw0rd",
                        "enabled": True,
                        "test_profile": "solutions",
                    },
                    {
                        "id": "stage_statuses",
                        "username": "playwright_user_4",
                        "password": "P@ssw0rd",
                        "enabled": True,
                        "test_profile": "stage_statuses",
                    },
                    {
                        "id": "question_metrics",
                        "username": "playwright_user_5",
                        "password": "P@ssw0rd",
                        "enabled": True,
                        "test_profile": "question_metrics",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    users, errors = load_test_drive_users(users_file)

    assert errors == []
    assert [(user.username, user.test_profile) for user in users] == [
        ("playwright_user_1", "perfect"),
        ("playwright_user_2", "answers_and_hints"),
        ("playwright_user_3", "solutions"),
        ("playwright_user_4", "stage_statuses"),
        ("playwright_user_5", "question_metrics"),
    ]
    assert all(not hasattr(user, "administrator") for user in users)
