from collections.abc import Awaitable, Callable, Collection
from typing import TypeVar
from urllib.parse import urlsplit

import settings
from playwright.async_api import Page, Response
from utils.logging import log_step

T = TypeVar("T")


def response_path_is(
    response: Response,
    *,
    path: str,
    method: str | None = None,
) -> bool:
    if method is not None and response.request.method.upper() != method.upper():
        return False
    return urlsplit(response.url).path == path


def response_is_mutation(
    response: Response,
    *,
    resource_types: Collection[str] = ("xhr", "fetch", "document"),
) -> bool:
    return (
        response.request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and response.request.resource_type in resource_types
    )


def same_origin_content_response(response: Response, *, base_url: str) -> bool:
    response_url = urlsplit(response.url)
    expected_url = urlsplit(base_url)
    if (
        response_url.scheme,
        response_url.hostname,
        response_url.port,
    ) != (
        expected_url.scheme,
        expected_url.hostname,
        expected_url.port,
    ):
        return False

    return response.request.resource_type in {"document", "xhr", "fetch"}


def _log_response(description: str, response: Response) -> None:
    path = urlsplit(response.url).path
    log_step(
        f"[NETWORK] {description}: "
        f"{response.request.method} {path} -> {response.status}"
    )


def _assert_response_ok(description: str, response: Response) -> None:
    if response.ok:
        return
    path = urlsplit(response.url).path
    raise AssertionError(
        f"Сетевое действие '{description}' завершилось HTTP {response.status}: {path}"
    )


async def perform_and_wait_for_response(
    page: Page,
    action: Callable[[], Awaitable[T]],
    *,
    predicate: Callable[[Response], bool],
    description: str,
    timeout_ms: int = settings.NETWORK_TIMEOUT_MS,
    require_ok: bool = True,
) -> tuple[T, Response]:
    async with page.expect_response(predicate, timeout=timeout_ms) as response_info:
        result = await action()

    response = await response_info.value
    _log_response(description, response)
    if require_ok:
        _assert_response_ok(description, response)
    return result, response


async def perform_and_wait_for_dom_with_network_observation(
    page: Page,
    action: Callable[[], Awaitable[T]],
    wait_for_dom: Callable[[], Awaitable[None]],
    *,
    predicate: Callable[[Response], bool],
    description: str,
    require_ok: bool = True,
) -> tuple[T, Response | None]:
    """Ставит сетевой наблюдатель до клика и всегда подтверждает итог по DOM.

    Переходы между уже загруженными модулями могут выполняться полностью в SPA
    и не создавать отдельного запроса. Поэтому отсутствие запроса до готовности
    целевого DOM логируется, но не превращается в искусственное ожидание.
    """

    observed: list[Response] = []

    def on_response(response: Response) -> None:
        if not observed and predicate(response):
            observed.append(response)

    page.on("response", on_response)
    try:
        result = await action()
        await wait_for_dom()
    finally:
        page.remove_listener("response", on_response)

    response = observed[0] if observed else None
    if response is None:
        log_step(
            f"[NETWORK] {description}: целевой DOM готов; "
            "отдельный запрос не потребовался"
        )
        return result, None

    _log_response(description, response)
    if require_ok:
        _assert_response_ok(description, response)
    return result, response
