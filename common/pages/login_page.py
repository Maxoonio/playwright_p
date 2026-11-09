import settings
from playwright.async_api import Page, expect
from utils.logging import log_step
from utils.network import (
    perform_and_wait_for_response,
    response_path_is,
)
from utils.ui import safe_click


class LoginPage:
    def __init__(self, page: Page) -> None:
        self.page = page

    def username_field(self):
        return self.page.locator("#login")

    def password_field(self):
        return self.page.locator("#password")

    def submit_button(self):
        return self.page.get_by_role("button", name="Войти").first

    async def login_form_visible(self) -> bool:
        return (
            await self.username_field().is_visible()
            or await self.password_field().is_visible()
            or await self.submit_button().is_visible()
            or "/login" in self.page.url
        )

    async def open(self) -> None:
        response = await self.page.goto(
            settings.LOGIN_URL,
            wait_until="domcontentloaded",
            timeout=settings.APP_START_TIMEOUT_MS,
        )
        if response is not None:
            log_step(
                f"[NETWORK] Открытие страницы входа: "
                f"{response.request.method} /login -> {response.status}"
            )
            if not response.ok:
                raise AssertionError(f"Страница входа вернула HTTP {response.status}")

        await expect(self.username_field()).to_be_visible(
            timeout=settings.APP_START_TIMEOUT_MS
        )
        await expect(self.password_field()).to_be_visible(
            timeout=settings.APP_START_TIMEOUT_MS
        )
        await expect(self.submit_button()).to_be_visible(
            timeout=settings.APP_START_TIMEOUT_MS
        )
        log_step("[LOGIN] Страница входа открыта")

    async def login(self, username: str, password: str) -> None:
        last_error: Exception | None = None

        for attempt in range(1, settings.LOGIN_MAX_ATTEMPTS + 1):
            await self.username_field().fill(username)
            await self.password_field().fill(password)
            log_step(f"[LOGIN] Учетные данные пользователя введены (попытка {attempt})")

            try:
                await perform_and_wait_for_response(
                    self.page,
                    lambda: safe_click(self.submit_button()),
                    predicate=lambda response: response_path_is(
                        response,
                        path="/api/login/",
                        method="POST",
                    ),
                    description="Авторизация в PT Matrix Test Drive",
                )
                await self._wait_for_login_form_gone()
                log_step(f"[LOGIN] Выполнен вход как {username}")
                return
            except Exception as exc:
                last_error = exc

            if attempt < settings.LOGIN_MAX_ATTEMPTS:
                log_step(
                    "[LOGIN] Форма входа осталась видимой; "
                    "повторяем ввод учетных данных"
                )

        raise AssertionError(
            "Форма входа осталась видимой после повторных попыток"
        ) from last_error

    async def _wait_for_login_form_gone(self) -> None:
        if await self.login_form_visible():
            raise AssertionError(
                "Страница входа PT Matrix снова появилась после успешного "
                "ответа /api/login/"
            )
