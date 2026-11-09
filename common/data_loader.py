import json
from pathlib import Path

from models import (
    SUPPORTED_PROFILES,
    TestDriveUser,
)


def load_test_drive_users(
    path: Path,
) -> tuple[list[TestDriveUser], list[dict[str, str]]]:
    """Читает пользовательские учетные данные."""

    with path.open(encoding="utf-8") as file:
        payload = json.load(file)

    raw_users = payload.get("users")
    if not isinstance(raw_users, list):
        raise AssertionError(f"Ожидался массив 'users' в {path}")

    users: list[TestDriveUser] = []
    errors: list[dict[str, str]] = []

    for index, raw_user in enumerate(raw_users, start=1):
        if not isinstance(raw_user, dict):
            errors.append(
                {
                    "id": f"user_{index}",
                    "username": f"user_{index}",
                    "profile": "unknown",
                    "message": "Конфигурация пользователя должна быть объектом",
                }
            )
            continue

        user_id = str(raw_user.get("id", f"user_{index}")).strip()
        username = str(raw_user.get("username", "")).strip()
        password = str(raw_user.get("password", ""))
        profile = str(raw_user.get("test_profile", "")).strip()
        enabled = bool(raw_user.get("enabled", False))

        if not username or not password:
            errors.append(
                {
                    "id": user_id,
                    "username": username or user_id,
                    "profile": profile or "unknown",
                    "message": "Не указаны логин или пароль пользователя",
                }
            )
            continue

        if profile not in SUPPORTED_PROFILES:
            errors.append(
                {
                    "id": user_id,
                    "username": username,
                    "profile": profile or "unknown",
                    "message": f"Неизвестный профиль '{profile}'",
                }
            )
            continue

        users.append(
            TestDriveUser(
                id=user_id,
                username=username,
                password=password,
                enabled=enabled,
                test_profile=profile,
            )
        )

    return users, errors
