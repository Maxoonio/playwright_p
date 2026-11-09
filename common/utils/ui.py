import settings
from playwright.async_api import Locator, expect


async def safe_click(
    locator: Locator,
    *,
    timeout: int = settings.DEFAULT_TIMEOUT_MS,
) -> None:
    await locator.wait_for(state="visible", timeout=timeout)
    await locator.scroll_into_view_if_needed(timeout=timeout)
    await expect(locator).to_be_enabled(timeout=timeout)
    await locator.click(timeout=timeout)
