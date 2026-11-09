import shutil
import tempfile
from pathlib import Path

import settings
from playwright.async_api import Browser, BrowserContext, Page, Playwright
from utils.logging import log_step

_BROWSER_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
)


def _viewport_size() -> dict[str, int]:
    return {
        "width": settings.VIEWPORT_WIDTH,
        "height": settings.VIEWPORT_HEIGHT,
    }


def _browser_args() -> list[str]:
    window_arg = (
        f"--window-size={settings.VIEWPORT_WIDTH},{settings.VIEWPORT_HEIGHT}"
        if settings.HEADLESS
        else "--start-maximized"
    )
    return [
        "--incognito",
        "--lang=ru-RU",
        window_arg,
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling",
    ]


def resolve_browser_executable() -> Path | None:
    for candidate in _BROWSER_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


async def launch_browser(playwright: Playwright) -> Browser:
    launch_kwargs: dict[str, object] = {
        "headless": settings.HEADLESS,
        "slow_mo": settings.SLOW_MO_MS,
        "chromium_sandbox": True,
        "args": _browser_args(),
    }

    executable = resolve_browser_executable()
    if executable is not None:
        launch_kwargs["executable_path"] = str(executable)
        log_step(f"[BROWSER] Используется системный Chrome: {executable}")

    browser = await playwright.chromium.launch(**launch_kwargs)
    log_step("[BROWSER] Chromium запущен")
    return browser


async def create_context(browser: Browser) -> BrowserContext:
    context_kwargs: dict[str, object] = {
        "locale": settings.LOCALE,
        "extra_http_headers": {"Accept-Language": "ru-RU,ru;q=0.9"},
    }
    if settings.HEADLESS:
        viewport = _viewport_size()
        context_kwargs["viewport"] = viewport
        context_kwargs["screen"] = viewport
    else:
        context_kwargs["no_viewport"] = True

    context = await browser.new_context(**context_kwargs)
    context.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
    context.set_default_navigation_timeout(settings.DEFAULT_TIMEOUT_MS)
    log_step("[BROWSER] Создан изолированный контекст пользователя")
    return context


async def launch_incognito_context(
    playwright: Playwright,
) -> BrowserContext:
    user_data_dir = Path(tempfile.mkdtemp(prefix="pt-test-drive-chrome-"))
    launch_kwargs: dict[str, object] = {
        "headless": settings.HEADLESS,
        "slow_mo": settings.SLOW_MO_MS,
        "chromium_sandbox": True,
        "locale": settings.LOCALE,
        "extra_http_headers": {"Accept-Language": "ru-RU,ru;q=0.9"},
        "args": _browser_args(),
    }
    if settings.HEADLESS:
        viewport = _viewport_size()
        launch_kwargs["viewport"] = viewport
        launch_kwargs["screen"] = viewport
    else:
        launch_kwargs["no_viewport"] = True

    executable = resolve_browser_executable()
    if executable is not None:
        launch_kwargs["executable_path"] = str(executable)
        log_step(f"[BROWSER] Используется системный Chrome: {executable}")

    try:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            **launch_kwargs,
        )
    except Exception:
        shutil.rmtree(user_data_dir, ignore_errors=True)
        raise

    setattr(context, "_pt_user_data_dir", user_data_dir)
    context.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
    context.set_default_navigation_timeout(settings.DEFAULT_TIMEOUT_MS)
    log_step("[BROWSER] Запущен отдельный incognito-браузер пользователя")
    return context


async def close_context(context: BrowserContext) -> None:
    user_data_dir = getattr(context, "_pt_user_data_dir", None)
    await context.close()
    if user_data_dir is not None:
        shutil.rmtree(user_data_dir, ignore_errors=True)


async def maximize_page_window(page: Page) -> None:
    if settings.HEADLESS:
        await page.set_viewport_size(_viewport_size())
        return

    try:
        session = await page.context.new_cdp_session(page)
        window = await session.send("Browser.getWindowForTarget")
        window_id = window.get("windowId")
        if window_id is not None:
            await session.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window_id,
                    "bounds": {"windowState": "maximized"},
                },
            )
    except Exception:
        return
