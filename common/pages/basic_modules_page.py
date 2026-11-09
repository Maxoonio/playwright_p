from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import settings
from models import BasicExercise, BasicModule
from playwright.async_api import Frame, Locator, Page, Response, expect
from utils.browser import maximize_page_window
from utils.logging import compact_exception_message, log_step
from utils.network import (
    perform_and_wait_for_dom_with_network_observation,
    perform_and_wait_for_response,
    response_is_mutation,
    response_path_is,
    same_origin_content_response,
)
from utils.ui import safe_click


@dataclass(frozen=True)
class ProductInterval:
    start_date: str
    start_time: str
    end_date: str
    end_time: str

    def __str__(self) -> str:
        return f"{self.start_date} {self.start_time} - {self.end_date} {self.end_time}"


class QuestionContentMismatchError(AssertionError):
    """GitLab question content does not match the current web/DB content."""


class QuestionAnswerRejectedError(AssertionError):
    """The web explicitly rejected an answer loaded from GitLab."""


class QuestionInteractionError(AssertionError):
    """A question could not be operated after several UI retries."""


class ProductUnavailableError(AssertionError):
    """The external product did not expose a usable DOM in time."""


ProductDomScope = Page | Frame


def normalize_windows_path_escaping(value: str) -> str:
    """Collapse literal doubled separators only in drive-rooted Windows paths.

    JSON parsing already turns ``\\\\`` from the source document into one
    runtime ``\\``.  This guard handles content providers that return the
    escaped representation literally, while leaving normal paths, UNC paths,
    regular expressions, and arbitrary answer text unchanged.
    """

    if not re.match(r"^[A-Za-z]:\\", value) or "\\\\" not in value:
        return value
    return re.sub(r"\\{2,}", lambda _match: "\\", value)


@dataclass(frozen=True)
class QuestionAnswerResult:
    already_completed: bool
    failed_status_confirmed: bool


class BasicModulesPage:
    """DOM- и network-ориентированный драйвер EX-модулей test-drive."""

    _KNOWN_STATUSES = {
        "started",
        "completed",
        "skipped_by_bs",
        "undefined",
        "partial_success",
    }

    def __init__(self, page: Page) -> None:
        self.page = page
        self._reactivate_next_active_question = False

    def module_step(self, step_id: int) -> Locator:
        return self.page.locator(f"#scenario_step_id_{step_id}").first

    def section_header(self, section_id: int, title: str) -> Locator:
        title_locator = self.page.get_by_text(title, exact=True)
        return self.page.locator(f"#id_{section_id}").filter(has=title_locator).first

    def question_card(self, order: int) -> Locator:
        return (
            self.page.locator(".scenarios-basic-result__tasks-container:visible")
            .locator(f"#ex_{order}")
            .first
        )

    def product_link(self) -> Locator:
        return (
            self.page.locator(
                "a[target='_blank']:visible, span.statistics-link-cursor:visible"
            )
            .filter(
                has_text=re.compile(
                    rf"^\s*{re.escape(settings.PRODUCT_NAME)}\s*$",
                    re.IGNORECASE,
                )
            )
            .first
        )

    async def open_module(self, module: BasicModule) -> None:
        step = self.module_step(module.step_id)

        # Пункт активного модуля может быть скрыт свернутым разделом слева,
        # хотя его содержимое уже открыто в центральной области. В таком
        # состоянии не требуем визуальной доступности карточки навигации.
        if await self._module_is_active(module.step_id, module.title):
            await self._wait_for_module_active(module.step_id, module.title)
            await self._verify_module_iframe_if_available(module)
            log_step(f"[MODULE {module.step_id}] Открыт «{module.title}»")
            return

        if not await step.is_visible():
            opened_with_arrows = await self._navigate_hidden_module_with_arrows(module)
            if opened_with_arrows:
                await self._verify_module_iframe_if_available(module)
                log_step(f"[MODULE {module.step_id}] Открыт «{module.title}»")
                return

        await self._ensure_module_visible(module)

        async def wait_for_module_dom() -> None:
            await expect(step).to_be_visible()
            await expect(
                step.locator(".actionbar__step-title")
                .get_by_text(
                    module.title,
                    exact=True,
                )
                .first
            ).to_be_visible()
            await expect(step).to_have_class(
                re.compile(r"\bstep_active\b"),
                timeout=settings.DOM_TIMEOUT_MS,
            )
            await self._verify_module_iframe_if_available(module)

        classes = (await step.get_attribute("class")) or ""
        if "step_active" not in classes.split():
            await perform_and_wait_for_dom_with_network_observation(
                self.page,
                lambda: safe_click(step),
                wait_for_module_dom,
                predicate=self._matrix_content_response,
                description=(f"Открытие EX-модуля {module.step_id} «{module.title}»"),
            )
        else:
            await wait_for_module_dom()

        log_step(f"[MODULE {module.step_id}] Открыт «{module.title}»")

    async def go_to_next_module(
        self,
        module: BasicModule,
        *,
        require_unfinished_dialog: bool = False,
    ) -> None:
        if require_unfinished_dialog:
            await self.go_to_next_step(
                expected_step_id=module.step_id,
                expected_title=module.title,
                require_unfinished_dialog=True,
            )
            return

        if await self._module_is_active(
            module.step_id,
            module.title,
        ):
            return
        await self.open_module(module)

    async def go_to_next_step(
        self,
        *,
        expected_step_id: int,
        expected_title: str,
        require_unfinished_dialog: bool = False,
    ) -> None:
        if await self._module_is_active(
            expected_step_id,
            expected_title,
        ):
            if require_unfinished_dialog:
                raise AssertionError(
                    "Целевой модуль уже открыт: окно перехода из "
                    "незавершенного модуля не было проверено"
                )
            return

        await self._click_next_step(
            require_unfinished_dialog=require_unfinished_dialog,
        )
        await self._wait_for_module_active(
            expected_step_id,
            expected_title,
        )

    async def _click_next_step(
        self,
        *,
        require_unfinished_dialog: bool = False,
    ) -> int | None:
        """Переходит ровно на один модуль штатной правой стрелкой."""

        previous_step_id = await self._active_module_step_id()
        next_button = self.page.locator("button#right-step-btn:visible").first
        dialog = self.page.locator("matrix-next-module-dialog:visible").first

        async def click_and_handle_transition() -> None:
            await safe_click(next_button)
            await self.page.wait_for_function(
                r"""
                ({ previousStepId }) => {
                    const visible = (element) => Boolean(
                        element
                        && getComputedStyle(element).visibility !== "hidden"
                        && getComputedStyle(element).display !== "none"
                        && element.getClientRects().length
                    );
                    const dialog = document.querySelector(
                        "matrix-next-module-dialog"
                    );
                    const active = Array.from(document.querySelectorAll(
                        "[id^='scenario_step_id_'].step_active"
                    )).find(element => /^scenario_step_id_\d+$/.test(element.id));
                    const activeStepId = active
                        ? Number(active.id.replace("scenario_step_id_", ""))
                        : null;
                    return visible(dialog)
                        || (
                            activeStepId !== null
                            && activeStepId !== previousStepId
                        );
                }
                """,
                arg={"previousStepId": previous_step_id},
                timeout=settings.DOM_TIMEOUT_MS,
            )

            dialog_visible = await dialog.count() > 0
            if require_unfinished_dialog and not dialog_visible:
                raise AssertionError(
                    "При переходе из незавершенного модуля не "
                    "появилось предупреждающее окно"
                )

            if not dialog_visible:
                return

            await expect(
                dialog.get_by_text(
                    re.compile(
                        r"задания текущего еще не выполнены полностью",
                        re.IGNORECASE,
                    )
                ).first
            ).to_be_visible()
            submit = (
                dialog.locator("button#submitBtn:visible")
                .filter(
                    has_text=re.compile(
                        r"К следующему модулю",
                        re.IGNORECASE,
                    )
                )
                .first
            )
            await safe_click(submit)
            await expect(dialog).to_be_hidden(timeout=settings.DOM_TIMEOUT_MS)

        async def wait_for_next_dom() -> None:
            await self.page.wait_for_function(
                r"""
                ({ previousStepId }) => {
                    const active = Array.from(document.querySelectorAll(
                        "[id^='scenario_step_id_'].step_active"
                    )).find(element => /^scenario_step_id_\d+$/.test(element.id));
                    if (!active) return false;
                    const activeStepId = Number(
                        active.id.replace("scenario_step_id_", "")
                    );
                    return activeStepId !== previousStepId;
                }
                """,
                arg={"previousStepId": previous_step_id},
                timeout=settings.DOM_TIMEOUT_MS,
            )

        await perform_and_wait_for_dom_with_network_observation(
            self.page,
            click_and_handle_transition,
            wait_for_next_dom,
            predicate=self._matrix_content_response,
            description="Переход кнопкой к следующему модулю",
        )
        return await self._active_module_step_id()

    async def _navigate_hidden_module_with_arrows(
        self,
        module: BasicModule,
    ) -> bool:
        """Ищет скрытый пункт модуля последовательными переходами вперед.

        Метод общий для всех продуктов и профилей. Ограничение и контроль
        уже посещенных step_id не дают карусели зациклиться на поврежденном
        или неожиданном DOM.
        """

        visited_step_ids: set[int] = set()
        for _ in range(64):
            if await self._module_is_active(module.step_id, module.title):
                await self._wait_for_module_active(module.step_id, module.title)
                return True

            current_step_id = await self._active_module_step_id()
            if current_step_id is not None:
                if current_step_id in visited_step_ids:
                    return False
                visited_step_ids.add(current_step_id)

            next_button = self.page.locator("button#right-step-btn:visible").first
            if not await next_button.count() or not await next_button.is_visible():
                return False

            try:
                next_step_id = await self._click_next_step()
            except Exception as exc:
                if await self._module_is_active(module.step_id, module.title):
                    await self._wait_for_module_active(
                        module.step_id,
                        module.title,
                    )
                    return True
                log_step(
                    f"[MODULE {module.step_id}] Переход правой стрелкой "
                    "остановлен; используется навигация по дереву: "
                    f"{compact_exception_message(exc)}"
                )
                return False

            if await self._module_is_active(module.step_id, module.title):
                await self._wait_for_module_active(module.step_id, module.title)
                return True
            if next_step_id is None or next_step_id == current_step_id:
                return False

        return False

    async def _active_module_step_id(self) -> int | None:
        raw_step_id = await self.page.evaluate(
            r"""
            () => {
                const active = Array.from(document.querySelectorAll(
                    "[id^='scenario_step_id_'].step_active"
                )).find(element => /^scenario_step_id_\d+$/.test(element.id));
                if (!active) return null;
                return Number(active.id.replace("scenario_step_id_", ""));
            }
            """
        )
        return int(raw_step_id) if raw_step_id is not None else None

    async def ensure_module_for_answers(
        self,
        module: BasicModule,
    ) -> None:
        """Молча восстанавливает нужный модуль перед работой с вопросом."""

        for _ in range(3):
            module_is_active = await self._module_is_active(
                module.step_id,
                module.title,
            )
            if module_is_active:
                if await self._visible_tasks_match(module):
                    return
                await self._open_tasks_silently(module)
                if await self._visible_tasks_match(module):
                    return
                continue

            await self.open_module(module)
            await self._open_tasks_silently(module)
            if await self._visible_tasks_match(module):
                return

        raise AssertionError(
            f"Не удалось восстановить модуль {module.step_id} "
            f"«{module.title}» перед ответами"
        )

    async def verify_copyable_filters(
        self,
        module: BasicModule,
    ) -> tuple[str, ...]:
        """Проверяет каждый копируемый блок по новому DOM-toast."""

        # Для проверки фильтра достаточно точного active-модуля и живого body.
        # Angular иногда оставляет src iframe пустым/устаревшим, хотя нужный
        # документ уже отрисован, поэтому здесь не используем строгую
        # _ensure_module_content() с проверкой имени файла.
        await self.open_module(module)
        iframe = self.page.locator("iframe.scenarios-basic-result__iframe").first
        await expect(iframe).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

        frame = self.page.frame_locator("iframe.scenarios-basic-result__iframe")
        await expect(frame.locator("body")).to_be_visible(
            timeout=settings.DOM_TIMEOUT_MS
        )
        filters = frame.locator(
            ".code-container:has(pre code)[onclick*='copy'], "
            ".code-container:has(pre code):has([onclick*='copy']), "
            ".code-container:has(pre code):has(button[class*='copy']), "
            ".code-container:has(pre code):has([class*='copy-icon']), "
            ".code-container:has(pre code):has(img[src*='copy'])"
        )
        filter_count = await filters.count()
        if filter_count == 0:
            return ()

        copied_filters: list[str] = []
        for index in range(filter_count):
            filter_container = filters.nth(index)
            await expect(filter_container).to_be_visible()
            await filter_container.scroll_into_view_if_needed()
            filter_text = (
                await filter_container.locator("pre code").first.inner_text()
            ).strip()
            if not filter_text:
                raise AssertionError(f"Копируемый фильтр #{index + 1} пуст")

            click_target = filter_container
            container_onclick = (
                (await filter_container.get_attribute("onclick")) or ""
            ).casefold()
            if "copy" not in container_onclick:
                nested_copy_control = filter_container.locator(
                    "[onclick*='copy']:visible, "
                    "button[class*='copy']:visible, "
                    "[class*='copy-icon']:visible, "
                    "img[src*='copy']:visible"
                ).first
                if await nested_copy_control.count() > 0:
                    click_target = nested_copy_control

            toast_waiter = asyncio.create_task(self._wait_for_new_copy_toast())
            await asyncio.sleep(0)
            try:
                await safe_click(click_target)
                await toast_waiter
            finally:
                if not toast_waiter.done():
                    toast_waiter.cancel()
                    try:
                        await toast_waiter
                    except asyncio.CancelledError:
                        pass

            copied_filters.append(filter_text)

        return tuple(copied_filters)

    async def scroll_module_to_bottom_for_completion(
        self,
        module: BasicModule,
    ) -> None:
        """Прокручивает информационный iframe и подтверждает его низ по DOM."""

        await self._ensure_module_content(module)
        iframe = self.page.locator("iframe.scenarios-basic-result__iframe").first
        await expect(iframe).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        if module.content_filename:
            await expect(iframe).to_have_attribute(
                "src",
                re.compile(re.escape(module.content_filename)),
                timeout=settings.DOM_TIMEOUT_MS,
            )

        body = (
            self.page.frame_locator("iframe.scenarios-basic-result__iframe")
            .locator("body")
            .first
        )
        await expect(body).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        await iframe.scroll_into_view_if_needed()

        # Сначала воспроизводим пользовательскую прокрутку внутри iframe.
        # Затем доводим до конца каждый реальный scroll-container документа:
        # контент разных продуктов не всегда использует body как scroller.
        box = await iframe.bounding_box()
        if box is not None:
            await self.page.mouse.move(
                box["x"] + box["width"] / 2,
                box["y"]
                + min(
                    max(box["height"] / 2, 5),
                    max(box["height"] - 5, 5),
                ),
            )
            for _ in range(12):
                await self.page.mouse.wheel(0, 600)
                await self.page.wait_for_timeout(100)

        await body.evaluate(
            """
            (element) => {
                const document = element.ownerDocument;
                const view = document.defaultView;
                const candidates = Array.from(new Set([
                    document.scrollingElement,
                    document.documentElement,
                    document.body,
                    ...document.querySelectorAll("*")
                ].filter(Boolean)));

                for (const candidate of candidates) {
                    if (candidate.scrollHeight > candidate.clientHeight) {
                        candidate.scrollTop = candidate.scrollHeight;
                        candidate.dispatchEvent(
                            new Event("scroll", { bubbles: true })
                        );
                    }
                }
                view.scrollTo(0, document.documentElement.scrollHeight);
                document.dispatchEvent(
                    new Event("scroll", { bubbles: true })
                );
                view.dispatchEvent(
                    new Event("scroll", { bubbles: true })
                );
            }
            """
        )
        await self.page.wait_for_timeout(settings.THEORY_MODULE_COMPLETION_DWELL_MS)
        scroll_state = await body.evaluate(
            """
            (element) => {
                const document = element.ownerDocument;
                const view = document.defaultView;
                const root = document.scrollingElement
                    || document.documentElement;
                return {
                    scrollTop: view.scrollY || root.scrollTop,
                    viewportHeight: view.innerHeight,
                    scrollHeight: root.scrollHeight,
                };
            }
            """
        )
        bottom_gap = (
            scroll_state["scrollHeight"]
            - scroll_state["viewportHeight"]
            - scroll_state["scrollTop"]
        )
        if bottom_gap > 1:
            raise AssertionError(
                "Информационный iframe не достиг нижней границы: "
                f"остаток={bottom_gap}px"
            )
        log_step(
            f"[MODULE {module.step_id}] «{module.title}» "
            f"прокручен до конца (scrollTop={scroll_state['scrollTop']}, "
            f"scrollHeight={scroll_state['scrollHeight']})"
        )

    async def _verify_module_iframe_if_available(
        self,
        module: BasicModule,
    ) -> None:
        if not module.content_filename:
            return

        iframe = self.page.locator("iframe.scenarios-basic-result__iframe").first
        try:
            await expect(iframe).to_be_visible(timeout=3_000)
            src = (await iframe.get_attribute("src")) or ""
            if module.content_filename not in src:
                log_step(
                    f"[MODULE {module.step_id}] Активный модуль "
                    f"подтвержден по DOM, но iframe src={src!r} "
                    f"не содержит {module.content_filename!r}; "
                    "продолжаем к заданиям"
                )
                return
            await expect(
                self.page.frame_locator(
                    "iframe.scenarios-basic-result__iframe"
                ).locator("body")
            ).to_be_visible(timeout=3_000)
        except Exception as exc:
            log_step(
                f"[MODULE {module.step_id}] Активный модуль "
                "подтвержден по DOM, iframe не подтвердился: "
                f"{compact_exception_message(exc)}; продолжаем к заданиям"
            )

    async def verify_actionbar_toggle(self) -> None:
        expand = self.page.locator(".basic-scenario-actionbar-show:visible").first
        collapse = self.page.locator(".basic-actionbar__hide:visible").first

        if await collapse.count() > 0:
            await safe_click(collapse)
            await expect(expand).to_be_visible()

        await safe_click(expand)
        await expect(collapse).to_be_visible()
        await expect(collapse.get_by_text("Свернуть", exact=True)).to_be_visible()

        await safe_click(collapse)
        await expect(expand).to_be_visible()
        await expect(expand.get_by_text("Развернуть", exact=True)).to_be_visible()
        log_step("[MODULE][ACTIONBAR] Панель развернута и свернута")

    async def go_to_tasks(self, module: BasicModule) -> None:
        button = self.page.locator("button#goToTasksBtn:visible").first
        cards = self.page.locator(
            ".scenarios-basic-result__tasks-container:visible "
            "matrix-basic-scenario-task"
        )
        actionbar_hide = self.page.locator(".basic-actionbar__hide:visible").first

        async def wait_for_tasks_dom() -> None:
            await expect(cards.first).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
            await expect(actionbar_hide).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

        async def tasks_dom_is_open(timeout_ms: int) -> bool:
            try:
                await expect(cards.first).to_be_visible(timeout=timeout_ms)
                await expect(actionbar_hide).to_be_visible(timeout=timeout_ms)
                return True
            except Exception:
                return False

        if await tasks_dom_is_open(timeout_ms=750):
            log_step(f"[MODULE][TASKS] Tasks already open, cards={await cards.count()}")
            return

        if await button.count() == 0:
            await wait_for_tasks_dom()
            log_step(
                f"[MODULE][TASKS] Задания уже открыты, карточек={await cards.count()}"
            )
            return

        if not await button.is_enabled():
            if await tasks_dom_is_open(timeout_ms=1_000):
                log_step(
                    f"[MODULE][TASKS] Tasks already open, "
                    f"cards={await cards.count()}; goToTasksBtn disabled"
                )
                return

        await perform_and_wait_for_dom_with_network_observation(
            self.page,
            lambda: safe_click(button),
            wait_for_tasks_dom,
            predicate=self._matrix_content_response,
            description="Переход к заданиям EX-модуля",
        )
        log_step(
            f"[MODULE][TASKS] Боковая панель открыта, карточек={await cards.count()}"
        )

    async def verify_module_status_before(
        self,
        module: BasicModule,
        *,
        expected_status: str | None = None,
    ) -> None:
        status = await self.read_module_status(module)
        if expected_status is not None and status != expected_status:
            raise AssertionError(
                f"До выполнения модуль {module.step_id} имеет "
                f"{status}, ожидался {expected_status}"
            )
        if status not in {
            "undefined",
            "started",
            "partial_success",
            "completed",
        }:
            raise AssertionError(
                f"До выполнения модуль {module.step_id} имеет "
                f"некорректный статус {status}"
            )
        log_step(f"[STATUS][BEFORE] Модуль {module.step_id}: {status}")

        for section_id, title in zip(
            module.ancestor_section_ids,
            module.ancestor_section_titles,
            strict=True,
        ):
            section_status = await self.read_section_status(
                section_id,
                title,
            )
            if section_status not in self._KNOWN_STATUSES:
                raise AssertionError(
                    f"Неизвестный статус раздела «{title}»: {section_status}"
                )
            log_step(
                f"[STATUS][BEFORE] Раздел {section_id} «{title}»: {section_status}"
            )

    async def verify_module_status_after(
        self,
        module: BasicModule,
    ) -> None:
        await expect(
            self.module_step(module.step_id)
            .locator("img.actionbar__step[src*='success-filled.svg']")
            .first
        ).to_be_attached(timeout=settings.DOM_TIMEOUT_MS)
        status = await self.read_module_status(module)
        if status != "completed":
            raise AssertionError(
                f"После ответов модуль {module.step_id} имеет "
                f"статус {status}, ожидался completed"
            )
        log_step(f"[STATUS][AFTER] Модуль {module.step_id}: completed")

        for section_id, title in zip(
            module.ancestor_section_ids,
            module.ancestor_section_titles,
            strict=True,
        ):
            section_status = await self.read_section_status(
                section_id,
                title,
            )
            if section_status not in {
                "started",
                "partial_success",
                "completed",
            }:
                raise AssertionError(
                    f"После выполнения модуля раздел «{title}» "
                    f"имеет статус {section_status}"
                )
            log_step(f"[STATUS][AFTER] Раздел {section_id} «{title}»: {section_status}")

    async def read_module_status(self, module: BasicModule) -> str:
        root = self.module_step(module.step_id)
        # Свернутый раздел может либо оставить пункт скрытым в DOM, либо
        # полностью удалить его через Angular *ngIf. В первом случае статус
        # читается без визуального раскрытия; во втором раскрываем только
        # нужный раздел, чтобы вернуть узел и не спутать отсутствие DOM со
        # статусом модуля.
        if await root.count() == 0:
            await self._ensure_module_visible(module)
            root = self.module_step(module.step_id)
        return await self._read_status(root)

    async def read_module_statuses(
        self,
        modules: tuple[BasicModule, ...],
    ) -> dict[int, str]:
        raw_statuses = await self.page.evaluate(
            """
            (stepIds) => {
                const status = (element) => {
                    if (!element) return null;
                    const marker = (
                        `${element.className || ""} `
                        + `${element.innerHTML || ""}`
                    ).toLowerCase();
                    if (
                        marker.includes("skipped_by_bs")
                        || marker.includes("skipped-by-bs")
                        || marker.includes("skip-filled")
                        || marker.includes("skipped-filled")
                        || marker.includes("forbidden.svg")
                    ) return "skipped_by_bs";
                    if (
                        marker.includes("partial_success")
                        || marker.includes("partial-success")
                        || marker.includes("partialsuccess")
                        || marker.includes("warning-filled")
                        || marker.includes("success-yellow.svg")
                    ) return "partial_success";
                    if (
                        marker.includes("success-filled.svg")
                        || /\\b(completed|complete)\\b/.test(marker)
                    ) return "completed";
                    if (
                        marker.includes("circle-yellow")
                        || /\\bstarted\\b/.test(marker)
                    ) return "started";
                    if (
                        marker.includes("circle-gray")
                        || /\\bundefined\\b/.test(marker)
                    ) return "undefined";
                    return null;
                };

                return stepIds.map((stepId) => ({
                    stepId,
                    status: status(
                        document.querySelector(
                            `#scenario_step_id_${stepId}`
                        )
                    ),
                }));
            }
            """,
            [module.step_id for module in modules],
        )

        statuses: dict[int, str] = {}
        unresolved_step_ids: set[int] = set()
        for item in raw_statuses:
            step_id = int(item["stepId"])
            status = item["status"]
            if status is None:
                unresolved_step_ids.add(step_id)
                continue
            if status not in self._KNOWN_STATUSES:
                raise AssertionError(
                    f"Не удалось определить DOM-статус модуля {step_id}: {status!r}"
                )
            statuses[step_id] = status

        # Свернутые Angular-разделы могут удалять дочерние модули из DOM.
        # Раскрываем только недостающие разделы; уже считанные значения
        # сохраняем, потому что при раскрытии следующего раздела предыдущий
        # снова может исчезнуть из DOM.
        for module in modules:
            if module.step_id not in unresolved_step_ids:
                continue
            statuses[module.step_id] = await self.read_module_status(module)

        if len(statuses) != len(modules):
            raise AssertionError(
                "Количество считанных статусов модулей не совпало "
                f"с контентом: {len(statuses)} != {len(modules)}"
            )
        return statuses

    async def find_module_with_status(
        self,
        modules: tuple[BasicModule, ...],
        expected_status: str,
    ) -> BasicModule:
        if expected_status not in self._KNOWN_STATUSES:
            raise AssertionError(f"Неизвестный ожидаемый статус {expected_status!r}")

        observed: list[str] = []
        for module in reversed(modules):
            actual = await self.read_module_status(module)
            observed.append(f"{module.step_id}={actual}")
            if actual == expected_status:
                return module

        raise AssertionError(
            f"Не найден EX-модуль со статусом {expected_status}; "
            f"проверены: {', '.join(reversed(observed))}"
        )

    async def verify_question_status_expected(
        self,
        module: BasicModule,
        exercise: BasicExercise,
        expected_status: str,
    ) -> None:
        await self.ensure_module_for_answers(module)

        card = self.question_card(exercise.order)
        await expect(card).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        await self._wait_for_status(card, expected_status)
        actual = await self._read_status(card)
        if actual != expected_status:
            raise AssertionError(
                f"Вопрос Q{exercise.order} модуля {module.step_id} "
                f"«{module.title}»: ожидался статус "
                f"{expected_status}, получен {actual}"
            )
        log_step(
            f"[STATUS][QUESTION] Модуль {module.step_id}, Q{exercise.order}: {actual}"
        )

    async def read_section_status(
        self,
        section_id: int,
        title: str,
    ) -> str:
        section = self.section_header(section_id, title)
        await expect(section).to_be_visible()
        return await self._read_status(section)

    async def answer_question(
        self,
        module: BasicModule,
        exercise: BasicExercise,
        *,
        verify_incorrect_first: bool = True,
    ) -> QuestionAnswerResult:
        """Возвращает подтвержденные статусы карточки вопроса."""

        await self.ensure_module_for_answers(module)
        card = self.question_card(exercise.order)
        await expect(card).to_be_visible()
        await self._verify_question_text(card, exercise)

        if await self._question_is_completed(card):
            await self._verify_completed_question(card, exercise)
            log_step(
                f"[QUESTION][Q{exercise.order}] Уже completed; "
                "повторная отправка не выполняется"
            )
            return QuestionAnswerResult(
                already_completed=True,
                failed_status_confirmed=False,
            )

        await self._expect_question_opened_by_flow(card, exercise)
        await self._verify_question_text(card, exercise)

        failed_status_confirmed = False
        if verify_incorrect_first and exercise.question_type in {
            "input",
            "radio",
            "checkbox",
        }:
            await self._submit_incorrect_answer(card, exercise)
            failed_status_confirmed = True
            await self._open_question_for_retry(card, exercise)
            await self._verify_question_text(card, exercise)

        if exercise.question_type == "input":
            await self._fill_input_answer(card, exercise)
        elif exercise.question_type == "radio":
            await self._select_radio_answer(card, exercise)
        elif exercise.question_type == "checkbox":
            await self._select_checkbox_answers(card, exercise)
        elif exercise.question_type == "drag-n-drop":
            await self._arrange_drag_answers(card, exercise)
        else:
            raise AssertionError(f"Не поддержан тип вопроса {exercise.question_type!r}")

        await self._submit_correct_answer_with_retries(card, exercise)
        log_step(
            f"[QUESTION][STEP {exercise.step_id}][Q{exercise.order}] "
            f"{exercise.question_type} -> completed"
        )
        return QuestionAnswerResult(
            already_completed=False,
            failed_status_confirmed=failed_status_confirmed,
        )

    async def answer_question_with_retries(
        self,
        module: BasicModule,
        exercise: BasicExercise,
        *,
        attempts: int = 3,
        verify_incorrect_first: bool = True,
    ) -> QuestionAnswerResult:
        """Retry transient card/drag failures without hiding them as skipped."""

        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await self.answer_question(
                    module,
                    exercise,
                    verify_incorrect_first=verify_incorrect_first,
                )
            except (
                QuestionContentMismatchError,
                QuestionAnswerRejectedError,
            ):
                raise
            except Exception as exc:
                last_error = exc
                if attempt == attempts:
                    break
                await self._recover_question_for_retry(
                    module,
                    exercise,
                )

        raise QuestionInteractionError(
            f"Q{exercise.order}: DOM interaction failed after "
            f"{attempts} attempts: {last_error}"
        ) from last_error

    async def submit_incorrect_answer_only(
        self,
        module: BasicModule,
        exercise: BasicExercise,
    ) -> None:
        """Leave a supported question failed for module-status checks."""

        if exercise.question_type not in {"input", "radio", "checkbox"}:
            raise AssertionError(
                "Нельзя создать failed-статус для типа вопроса "
                f"{exercise.question_type!r}"
            )

        await self.ensure_module_for_answers(module)
        card = self.question_card(exercise.order)
        await expect(card).to_be_visible()
        if await self._question_is_completed(card):
            raise AssertionError(
                f"Q{exercise.order} уже completed; failed-статус создать нельзя"
            )

        await self._expect_question_opened_by_flow(card, exercise)
        await self._verify_question_text(card, exercise)
        await self._submit_incorrect_answer(card, exercise)

    async def _recover_question_for_retry(
        self,
        module: BasicModule,
        exercise: BasicExercise,
    ) -> None:
        self._reactivate_next_active_question = False
        await self.ensure_module_for_answers(module)
        card = self.question_card(exercise.order)
        await expect(card).to_be_visible()
        if await self._question_is_completed(card):
            return

        classes = (await card.get_attribute("class")) or ""
        if "basic-scenario-tasks-card__active" in classes.split():
            collapse = card.locator(
                "i[kbq-icon='kbq-chevron-up-s_24'], i.kbq-chevron-up-s_24"
            ).first
            await safe_click(collapse if await collapse.count() else card)

        await safe_click(card)
        await expect(card).to_have_class(
            re.compile(r"\bbasic-scenario-tasks-card__active\b"),
            timeout=settings.DOM_TIMEOUT_MS,
        )

    async def verify_module_status_expected(
        self,
        module: BasicModule,
        expected_status: str,
    ) -> None:
        root = self.module_step(module.step_id)
        await self._wait_for_status(root, expected_status)
        actual = await self._read_status(root)
        if actual != expected_status:
            raise AssertionError(
                f"Модуль {module.step_id} «{module.title}»: "
                f"ожидался статус {expected_status}, "
                f"получен {actual}"
            )
        log_step(f"[STATUS] Модуль {module.step_id} «{module.title}»: {actual}")

    async def verify_module_status_allowed(
        self,
        module: BasicModule,
        allowed_statuses: set[str],
    ) -> str:
        root = self.module_step(module.step_id)
        actual = await self._wait_for_allowed_status(
            root,
            allowed_statuses,
        )
        if actual not in allowed_statuses:
            raise AssertionError(
                f"Модуль {module.step_id} «{module.title}»: "
                f"ожидался один из статусов {sorted(allowed_statuses)}, "
                f"получен {actual}"
            )
        log_step(f"[STATUS] Модуль {module.step_id} «{module.title}»: {actual}")
        return actual

    async def verify_section_status_expected(
        self,
        section_id: int,
        title: str,
        expected_status: str,
    ) -> None:
        section = self.section_header(section_id, title)
        await expect(section).to_be_visible()
        await self._wait_for_status(section, expected_status)
        actual = await self._read_status(section)
        if actual != expected_status:
            raise AssertionError(
                f"Раздел {section_id} «{title}»: "
                f"ожидался статус {expected_status}, "
                f"получен {actual}"
            )
        log_step(f"[STATUS] Раздел {section_id} «{title}»: {actual}")

    async def verify_question_hint(
        self,
        module: BasicModule,
        exercise: BasicExercise,
    ) -> bool:
        """Open every visible hint and compare it with GitLab when available.

        Matrix can expose ``#hint-btn`` even when a freshly loaded content
        record has no ``hint_ru``.  The DOM button is authoritative for the
        interaction: it still has to be opened and closed so that an overlay
        cannot remain above the answer control.
        """

        await self.ensure_module_for_answers(module)
        card = self.question_card(exercise.order)
        await expect(card).to_be_visible()
        if await self._question_is_completed(card):
            log_step(
                f"[QUESTION][STEP {exercise.step_id}]"
                f"[Q{exercise.order}][HINT] Вопрос уже completed"
            )
            return False

        await self._expect_question_opened_by_flow(card, exercise)
        button = card.locator("button#hint-btn:visible").first
        if await button.count() == 0:
            if exercise.hint is not None:
                raise AssertionError(
                    f"Для подсказки шага {exercise.step_id}, Q{exercise.order} "
                    "не найдена видимая кнопка #hint-btn"
                )
            return False

        await safe_click(button)
        dialog = self.page.locator("matrix-basic-hint-dialog:visible").first
        await expect(dialog).to_be_visible()
        try:
            hint = dialog.locator("pre").first
            await expect(hint).to_be_visible()
            actual = _normalize_text(await hint.inner_text())
            if not actual:
                raise AssertionError(
                    f"Подсказка шага {exercise.step_id}, "
                    f"Q{exercise.order} открылась без текста"
                )

            if exercise.hint is not None:
                expected = _normalize_text(exercise.hint.replace("***", ""))
                if actual != expected:
                    raise AssertionError(
                        f"Подсказка шага {exercise.step_id}, "
                        f"Q{exercise.order} не совпала с GitLab: "
                        f"ожидалось={expected!r}, получено={actual!r}"
                    )
        finally:
            # Закрываем overlay даже при расхождении текста. Раньше исключение
            # оставляло модальное окно над полем ответа, после чего все четыре
            # стратегии ввода завершались на одном и том же click timeout.
            if await dialog.count() and await dialog.is_visible():
                close = dialog.locator("button#hintDialogCloseBtn:visible").first
                await safe_click(close)
                await expect(dialog).to_be_hidden()
            await self._restore_question_after_hint(module, exercise)

        log_step(
            f"[QUESTION][STEP {exercise.step_id}]"
            f"[Q{exercise.order}][HINT] Подсказка подтверждена"
        )
        return True

    async def _restore_question_after_hint(
        self,
        module: BasicModule,
        exercise: BasicExercise,
    ) -> None:
        """Reacquire the Angular card after the hint dialog is closed."""

        card = self.question_card(exercise.order)
        await expect(card).to_be_visible()
        if await self._question_is_completed(card):
            return
        await self._expect_question_opened_by_flow(card, exercise)
        send_button = card.locator("button#send-btn:visible").first
        await expect(send_button).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

    async def handle_module_completion_dialog(
        self,
        module: BasicModule,
    ) -> None:
        dialog = self.page.locator("matrix-congratulation-basic-stage:visible").first

        try:
            await expect(dialog).to_be_visible(timeout=2_000)
        except Exception:
            if module.required_for_ctf:
                log_step(
                    f"[MODULE {module.step_id}] "
                    "Поздравительное окно не показано; идем дальше"
                )
            return

        if module.required_for_ctf:
            await expect(
                dialog.get_by_text("Отличная работа!", exact=True)
            ).to_be_visible()
            await expect(
                dialog.get_by_text(re.compile(r"CTF сценарии", re.IGNORECASE))
            ).to_be_visible()
        elif await dialog.count() == 0:
            return

        button = dialog.locator("button#congratulationModalSubmit:visible").first

        async def wait_for_dialog_closed() -> None:
            await expect(dialog).to_be_hidden(timeout=settings.DOM_TIMEOUT_MS)

        await perform_and_wait_for_dom_with_network_observation(
            self.page,
            lambda: safe_click(button),
            wait_for_dialog_closed,
            predicate=self._matrix_content_response,
            description=(f"Продолжение после завершения модуля {module.step_id}"),
        )
        log_step(f"[MODULE {module.step_id}] Поздравительное окно закрыто")

    async def verify_product_interval(
        self,
        *,
        username: str,
        password: str,
        expected_interval: ProductInterval | None = None,
        expected_source: str = "Matrix",
        product_link: Locator | None = None,
        reopen_after_login: bool = True,
    ) -> None:
        expected = (
            expected_interval
            if expected_interval is not None
            else await self._read_matrix_solution_interval()
        )
        log_step(f"[PRODUCT][EXPECTED] {expected_source}: {expected}")
        await self._verify_product_link_contract(product_link)
        product_username = username
        product_password = password
        configured_product_username = getattr(
            settings,
            "PRODUCT_LOGIN",
            "",
        )
        configured_product_password = getattr(
            settings,
            "PRODUCT_PASSWORD",
            "",
        )
        if configured_product_username and configured_product_password:
            product_username = configured_product_username
            product_password = configured_product_password
        elif settings.PRODUCT_INTERVAL_KIND in {"siem", "sandbox"}:
            (
                product_username,
                product_password,
            ) = await self._read_product_credentials_from_dom(
                expected_username=username,
            )

        first_page = await self._open_product_page(product_link)
        nad_auth_only = settings.PRODUCT_INTERVAL_KIND == "nad" and reopen_after_login
        try:
            await self._login_product_if_needed(
                first_page,
                username=product_username,
                password=product_password,
                wait_for_app_after_submit=not nad_auth_only,
            )
            if nad_auth_only:
                settle_ms = getattr(
                    settings,
                    "PRODUCT_FIRST_LOGIN_SETTLE_MS",
                    5_000,
                )
                await first_page.wait_for_timeout(settle_ms)
            else:
                await self._close_product_release_notes_if_present(first_page)
            if not reopen_after_login:
                await self._verify_product_interval_on_page(
                    first_page,
                    expected=expected,
                    expected_source=expected_source,
                )
        finally:
            await self._close_external_page(first_page)

        if not reopen_after_login:
            self._reactivate_next_active_question = True
            return

        if settings.PRODUCT_INTERVAL_KIND == "nad":
            if expected_interval is None:
                expected = await self._read_matrix_solution_interval()
                log_step(
                    "[PRODUCT][EXPECTED] Matrix перед повторным "
                    f"переходом в PT NAD: {expected}"
                )
            log_step(
                "[PRODUCT][NAD] Первый переход завершен: продуктовая сессия "
                "активна; повторно открываем исходную ссылку Matrix для "
                "проверки интервала"
            )

        second_page = await self._open_product_page(product_link)
        try:
            await self._login_product_if_needed(
                second_page,
                username=product_username,
                password=product_password,
            )
            await self._close_product_release_notes_if_present(second_page)
            await self._verify_product_interval_on_page(
                second_page,
                expected=expected,
                expected_source=expected_source,
            )
        finally:
            await self._close_external_page(second_page)
        self._reactivate_next_active_question = True

    async def _verify_product_interval_on_page(
        self,
        product_page: Page,
        *,
        expected: ProductInterval,
        expected_source: str,
    ) -> None:
        if settings.PRODUCT_INTERVAL_KIND == "nad":
            actual = await self._read_nad_interval_fields(product_page)
        elif settings.PRODUCT_INTERVAL_KIND == "siem":
            actual = await self._read_siem_interval_fields(
                product_page,
                expected_interval=expected,
            )
        elif settings.PRODUCT_INTERVAL_KIND == "sandbox":
            actual = await self._read_sandbox_interval_fields(product_page)
        else:
            raise AssertionError(
                "Неизвестная стратегия чтения продуктового "
                f"интервала: {settings.PRODUCT_INTERVAL_KIND!r}"
            )
        log_step(f"[PRODUCT][{settings.PRODUCT_SLUG.upper()}] Интервал: {actual}")
        if not self._intervals_match(expected, actual):
            raise AssertionError(
                f"Интервал «{expected_source}» не совпал с интервалом "
                f"{settings.PRODUCT_NAME}: ожидаемый={expected}; "
                f"{settings.PRODUCT_NAME}={actual}"
            )

    async def _read_product_credentials_from_dom(
        self,
        *,
        expected_username: str,
    ) -> tuple[str, str]:
        """Читает выданные Matrix продуктовые credentials через DOM-copy."""

        username_line = self.page.get_by_text(
            re.compile(r"^\s*Логин:\s*\S+\s*$", re.IGNORECASE)
        ).first
        await expect(username_line).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        username_text = " ".join((await username_line.inner_text()).split())
        username_match = re.search(
            r"Логин:\s*(\S+)",
            username_text,
            re.IGNORECASE,
        )
        if username_match is None:
            raise AssertionError("Не удалось прочитать продуктовый логин из DOM Matrix")
        product_username = username_match.group(1)
        if product_username != expected_username:
            raise AssertionError(
                "Продуктовый логин Matrix не совпал с пользователем: "
                f"DOM={product_username!r}, "
                f"пользователь={expected_username!r}"
            )

        password_line = self.page.get_by_text(
            re.compile(r"^\s*Пароль:\s*\*+\s*$", re.IGNORECASE)
        ).first
        await expect(password_line).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        password_row = password_line.locator("xpath=..").first
        copy_button = password_row.locator("img.scenarios-tasks-copy-icon").first
        await expect(copy_button).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

        # launch_persistent_context не позволяет выдать clipboard permissions
        # через CDP во всех версиях Chrome. Перехватываем значение именно в
        # момент клика по DOM-кнопке: это проверяет пользовательский сценарий
        # копирования и не раскрывает пароль из fixture/API.
        try:
            await self.page.context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin=settings.BASE_URL,
            )
        except Exception:
            log_step(
                "[PRODUCT][CREDENTIALS] Chrome не поддержал выдачу "
                "clipboard permissions persistent-контексту; "
                "используется DOM-перехват операции копирования"
            )

        await self.page.evaluate(
            r"""
            () => {
                window.__ptMatrixCopiedText = "";
                const capture = value => {
                    const text = String(value ?? "");
                    if (text && !/^\*+$/.test(text)) {
                        window.__ptMatrixCopiedText = text;
                    }
                };

                const clipboard = navigator.clipboard;
                if (clipboard && typeof clipboard.writeText === "function") {
                    const originalWrite = clipboard.writeText.bind(clipboard);
                    try {
                        Object.defineProperty(clipboard, "writeText", {
                            configurable: true,
                            value: async value => {
                                capture(value);
                                try {
                                    return await originalWrite(value);
                                } catch (_) {
                                    return undefined;
                                }
                            },
                        });
                    } catch (_) {
                        // Ниже остается fallback через copy/execCommand.
                    }
                }

                const originalExecCommand =
                    typeof document.execCommand === "function"
                        ? document.execCommand.bind(document)
                        : null;
                if (originalExecCommand) {
                    document.execCommand = (command, ...args) => {
                        if (String(command).toLowerCase() === "copy") {
                            const active = document.activeElement;
                            if (
                                active instanceof HTMLInputElement ||
                                active instanceof HTMLTextAreaElement
                            ) {
                                const start = active.selectionStart ?? 0;
                                const end = active.selectionEnd ?? active.value.length;
                                capture(active.value.slice(start, end) || active.value);
                            } else {
                                capture(window.getSelection()?.toString());
                            }
                        }
                        return originalExecCommand(command, ...args);
                    };
                }

                document.addEventListener(
                    "copy",
                    event => {
                        capture(event.clipboardData?.getData("text/plain"));
                        const active = document.activeElement;
                        if (
                            active instanceof HTMLInputElement ||
                            active instanceof HTMLTextAreaElement
                        ) {
                            const start = active.selectionStart ?? 0;
                            const end = active.selectionEnd ?? active.value.length;
                            capture(active.value.slice(start, end) || active.value);
                        } else {
                            capture(window.getSelection()?.toString());
                        }
                    },
                    true,
                );
            }
            """
        )
        await safe_click(copy_button)
        await self.page.wait_for_function(
            r"""
            () => Boolean(
                window.__ptMatrixCopiedText &&
                !/^\*+$/.test(window.__ptMatrixCopiedText)
            )
            """,
            timeout=settings.DOM_TIMEOUT_MS,
        )
        product_password = await self.page.evaluate("() => window.__ptMatrixCopiedText")
        if not isinstance(product_password, str) or not product_password:
            raise AssertionError("Matrix скопировал пустой продуктовый пароль")

        log_step(
            "[PRODUCT][CREDENTIALS] Продуктовый логин подтвержден, "
            "пароль получен кнопкой копирования из DOM Matrix"
        )
        return product_username, product_password

    async def _ensure_module_visible(
        self,
        module: BasicModule,
    ) -> None:
        step = self.module_step(module.step_id)
        if await step.is_visible():
            return

        for section_id, title in zip(
            module.ancestor_section_ids,
            module.ancestor_section_titles,
            strict=True,
        ):
            if await step.is_visible():
                break
            section = self.section_header(section_id, title)
            await expect(section).to_be_visible()
            if not await self._section_is_expanded(section):
                await safe_click(section)
                await expect(
                    section.locator(
                        "i[kbq-icon='kbq-chevron-up-s_16'], i.kbq-chevron-up-s_16"
                    ).first
                ).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

        await expect(step).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

    async def _section_is_expanded(
        self,
        section: Locator,
    ) -> bool:
        aria_expanded = await section.get_attribute("aria-expanded")
        if aria_expanded is not None:
            return aria_expanded.casefold() == "true"

        expanded_icon = section.locator(
            "i[kbq-icon='kbq-chevron-up-s_16'], i.kbq-chevron-up-s_16"
        ).first
        if await expanded_icon.count() > 0:
            return await expanded_icon.is_visible()

        collapsed_icon = section.locator(
            "i[kbq-icon='kbq-chevron-down-s_16'], i.kbq-chevron-down-s_16"
        ).first
        if await collapsed_icon.count() > 0:
            return not await collapsed_icon.is_visible()

        return False

    async def _module_is_active(
        self,
        step_id: int,
        title: str,
    ) -> bool:
        step = self.module_step(step_id)
        if await step.count() == 0:
            return False
        return await step.evaluate(
            """
            (element, expectedTitle) => {
                const title = element.querySelector(
                    ".actionbar__step-title"
                )?.textContent || "";
                const normalize = (value) => value
                    .replace(/\\s+/g, " ")
                    .trim()
                    .toLocaleLowerCase();
                return element.classList.contains("step_active")
                    && normalize(title).includes(
                        normalize(expectedTitle)
                    );
            }
            """,
            title,
        )

    async def _wait_for_module_active(
        self,
        step_id: int,
        title: str,
    ) -> None:
        await self.page.wait_for_function(
            """
            ({ stepId, expectedTitle }) => {
                const element = document.querySelector(
                    `#scenario_step_id_${stepId}`
                );
                if (!element?.classList.contains("step_active")) {
                    return false;
                }
                const title = element.querySelector(
                    ".actionbar__step-title"
                )?.textContent || "";
                const normalize = (value) => value
                    .replace(/\\s+/g, " ")
                    .trim()
                    .toLocaleLowerCase();
                return normalize(title).includes(
                    normalize(expectedTitle)
                );
            }
            """,
            arg={
                "stepId": step_id,
                "expectedTitle": title,
            },
            timeout=settings.DOM_TIMEOUT_MS,
        )

    async def _visible_tasks_match(
        self,
        module: BasicModule,
    ) -> bool:
        if not await self._module_is_active(
            module.step_id,
            module.title,
        ):
            return False

        cards = self.page.locator(
            ".scenarios-basic-result__tasks-container:visible "
            "matrix-basic-scenario-task"
        )
        return await cards.count() > 0

    async def _ensure_module_content(
        self,
        module: BasicModule,
    ) -> None:
        iframe = self.page.locator("iframe.scenarios-basic-result__iframe").first
        for _ in range(3):
            active = await self._module_is_active(
                module.step_id,
                module.title,
            )
            source_matches = True
            if module.content_filename:
                source = (
                    (await iframe.get_attribute("src"))
                    if await iframe.count() > 0
                    else ""
                ) or ""
                source_matches = module.content_filename in source
            if active and source_matches:
                return

            await self._ensure_module_visible(module)
            await safe_click(self.module_step(module.step_id))
            await self._wait_for_module_active(
                module.step_id,
                module.title,
            )
            if module.content_filename:
                await expect(iframe).to_have_attribute(
                    "src",
                    re.compile(re.escape(module.content_filename)),
                    timeout=settings.DOM_TIMEOUT_MS,
                )

        raise AssertionError(
            f"Не удалось восстановить содержимое модуля "
            f"{module.step_id} «{module.title}»"
        )

    async def _open_tasks_silently(
        self,
        module: BasicModule,
    ) -> None:
        if await self._visible_tasks_match(module):
            return

        button = self.page.locator("button#goToTasksBtn:visible").first
        if await button.count() == 0:
            collapse = self.page.locator(".basic-actionbar__hide:visible").first
            if await collapse.count() > 0:
                await safe_click(collapse)
            await expect(button).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

        await safe_click(button)
        cards = self.page.locator(
            ".scenarios-basic-result__tasks-container:visible "
            "matrix-basic-scenario-task"
        )
        await expect(cards.first).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

    async def _wait_for_new_copy_toast(self) -> None:
        await self.page.evaluate(
            """
            (timeoutMs) => new Promise((resolve, reject) => {
                const successText = /Успешно скопировано в буфер/i;
                const visible = (element) => Boolean(
                    element
                    && getComputedStyle(element).visibility !== "hidden"
                    && getComputedStyle(element).display !== "none"
                    && element.getClientRects().length
                );
                const initialToasts = new Set(Array.from(
                    document.querySelectorAll(".kbq-toast__container")
                ).filter(visible));
                const hiddenAfterStart = new WeakSet();
                const changedAfterStart = new WeakSet();
                const findSuccessToast = () => Array.from(
                    document.querySelectorAll(
                        ".kbq-toast__container"
                    )
                ).find((element) => (
                    visible(element)
                    && successText.test(element.innerText || "")
                    && (
                        !initialToasts.has(element)
                        || hiddenAfterStart.has(element)
                        || changedAfterStart.has(element)
                    )
                ));

                const updateInitialToastState = () => {
                    for (const toast of initialToasts) {
                        if (!visible(toast)) hiddenAfterStart.add(toast);
                    }
                };

                const toastForNode = (node) => {
                    const element = node?.nodeType === Node.ELEMENT_NODE
                        ? node
                        : node?.parentElement;
                    if (!element) return null;
                    if (element.matches?.(".kbq-toast__container")) {
                        return element;
                    }
                    return element.closest?.(".kbq-toast__container") || null;
                };

                const observer = new MutationObserver((mutations) => {
                    updateInitialToastState();
                    for (const mutation of mutations) {
                        const changedToast = toastForNode(mutation.target);
                        if (changedToast && initialToasts.has(changedToast)) {
                            changedAfterStart.add(changedToast);
                        }
                        for (const node of mutation.addedNodes) {
                            const addedToast = toastForNode(node);
                            if (addedToast && initialToasts.has(addedToast)) {
                                changedAfterStart.add(addedToast);
                            }
                        }
                    }
                    if (findSuccessToast()) {
                        observer.disconnect();
                        clearTimeout(timer);
                        resolve(true);
                    }
                });
                observer.observe(document.documentElement, {
                    attributes: true,
                    childList: true,
                    characterData: true,
                    subtree: true
                });
                const timer = setTimeout(() => {
                    observer.disconnect();
                    reject(new Error(
                        "copy success toast timeout"
                    ));
                }, timeoutMs);
            })
            """,
            settings.DOM_TIMEOUT_MS,
        )
        toast = (
            self.page.locator(".kbq-toast__container:visible")
            .filter(
                has_text=re.compile(
                    r"Успешно скопировано в буфер",
                    re.IGNORECASE,
                )
            )
            .last
        )
        await expect(toast).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

    async def _read_status(self, root: Locator) -> str:
        await expect(root).to_be_attached()
        marker = (
            ((await root.get_attribute("class")) or "")
            + " "
            + (await root.inner_html())
        ).casefold()

        if (
            "skipped_by_bs" in marker
            or "skipped-by-bs" in marker
            or "skip-filled" in marker
            or "skipped-filled" in marker
            or "forbidden.svg" in marker
        ):
            return "skipped_by_bs"
        if (
            "partial_success" in marker
            or "partial-success" in marker
            or "partialsuccess" in marker
            or "warning-filled" in marker
            or "success-yellow.svg" in marker
        ):
            return "partial_success"
        if "success-filled.svg" in marker or re.search(
            r"\b(completed|complete)\b", marker
        ):
            return "completed"
        if "circle-yellow" in marker or re.search(r"\bstarted\b", marker):
            return "started"
        if "circle-gray" in marker or re.search(r"\bundefined\b", marker):
            return "undefined"
        raise AssertionError("Не удалось определить статус по DOM-классам и иконкам")

    async def _wait_for_status(
        self,
        root: Locator,
        expected_status: str,
    ) -> None:
        await self._wait_for_allowed_status(
            root,
            {expected_status},
        )

    async def _wait_for_allowed_status(
        self,
        root: Locator,
        allowed_statuses: set[str],
    ) -> str:
        if not allowed_statuses:
            raise AssertionError("Не задан ни один допустимый статус")
        unknown_statuses = allowed_statuses - self._KNOWN_STATUSES
        if unknown_statuses:
            raise AssertionError(
                f"Неизвестные ожидаемые статусы: {sorted(unknown_statuses)}"
            )

        await expect(root).to_be_attached()
        return await root.evaluate(
            """
            (element, { allowed, timeoutMs }) => new Promise(
                (resolve, reject) => {
                    const status = () => {
                        const marker = (
                            `${element.className || ""} `
                            + `${element.innerHTML || ""}`
                        ).toLowerCase();
                        if (
                            marker.includes("skipped_by_bs")
                            || marker.includes("skipped-by-bs")
                            || marker.includes("skip-filled")
                            || marker.includes("skipped-filled")
                            || marker.includes("forbidden.svg")
                        ) return "skipped_by_bs";
                        if (
                            marker.includes("partial_success")
                            || marker.includes("partial-success")
                            || marker.includes("partialsuccess")
                            || marker.includes("warning-filled")
                            || marker.includes("success-yellow.svg")
                        ) return "partial_success";
                        if (
                            marker.includes("success-filled.svg")
                            || /\\b(completed|complete)\\b/.test(marker)
                        ) return "completed";
                        if (
                            marker.includes("circle-yellow")
                            || /\\bstarted\\b/.test(marker)
                        ) return "started";
                        if (
                            marker.includes("circle-gray")
                            || /\\bundefined\\b/.test(marker)
                        ) return "undefined";
                        return null;
                    };

                    const initial = status();
                    if (allowed.includes(initial)) {
                        resolve(initial);
                        return;
                    }

                    const observer = new MutationObserver(() => {
                        const actual = status();
                        if (allowed.includes(actual)) {
                            observer.disconnect();
                            clearTimeout(timer);
                            resolve(actual);
                        }
                    });
                    observer.observe(element, {
                        attributes: true,
                        childList: true,
                        subtree: true
                    });
                    const timer = setTimeout(() => {
                        observer.disconnect();
                        reject(new Error(
                            `statuses ${allowed.join(", ")} timeout; `
                            + `actual=${status()}`
                        ));
                    }, timeoutMs);
                }
            )
            """,
            {
                "allowed": sorted(allowed_statuses),
                "timeoutMs": settings.DOM_TIMEOUT_MS,
            },
        )

    async def _verify_question_text(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        question = card.locator("pre").first
        await expect(question).to_be_visible()
        actual = _normalize_text(await question.inner_text())
        expected = _normalize_text(exercise.question)
        if actual != expected:
            raise QuestionContentMismatchError(
                f"Текст Q{exercise.order} не совпал с GitLab: "
                f"ожидалось «{exercise.question}», "
                f"получено «{await question.inner_text()}»"
            )

    async def _expect_question_opened_by_flow(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        if self._reactivate_next_active_question:
            self._reactivate_next_active_question = False
            await self._reactivate_question_card(card, exercise)
            return

        classes = (await card.get_attribute("class")) or ""
        if "basic-scenario-tasks-card__active" not in classes.split():
            await safe_click(card)
        await expect(card).to_have_class(
            re.compile(r"\bbasic-scenario-tasks-card__active\b"),
            timeout=settings.DOM_TIMEOUT_MS,
        )

    async def _reactivate_question_card(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        classes = (await card.get_attribute("class")) or ""
        if "basic-scenario-tasks-card__active" in classes.split():
            collapse = card.locator(
                "i[kbq-icon='kbq-chevron-up-s_24'], i.kbq-chevron-up-s_24"
            ).first
            await safe_click(collapse if await collapse.count() else card)

        await safe_click(card)
        await expect(card).to_have_class(
            re.compile(r"\bbasic-scenario-tasks-card__active\b"),
            timeout=settings.DOM_TIMEOUT_MS,
        )

    async def _open_question_for_retry(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        classes = (await card.get_attribute("class")) or ""
        if "basic-scenario-tasks-card__active" not in classes.split():
            await safe_click(card)
        await expect(card).to_have_class(
            re.compile(r"\bbasic-scenario-tasks-card__active\b"),
            timeout=settings.DOM_TIMEOUT_MS,
        )
        log_step(
            f"[QUESTION][STEP {exercise.step_id}][Q{exercise.order}] "
            "card opened again after incorrect answer"
        )

    async def _submit_incorrect_answer(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        if exercise.question_type == "input":
            await self._fill_incorrect_input_answer(card, exercise)
        elif exercise.question_type == "radio":
            await self._select_incorrect_radio_answer(card, exercise)
        elif exercise.question_type == "checkbox":
            await self._select_incorrect_checkbox_answers(card, exercise)
        else:
            return

        await self._submit_current_answer(
            card,
            exercise,
            attempt="incorrect",
        )
        await self._verify_incorrect_question(card, exercise)
        log_step(
            f"[QUESTION][STEP {exercise.step_id}][Q{exercise.order}] "
            f"{exercise.question_type} incorrect answer confirmed"
        )

    async def _submit_current_answer(
        self,
        card: Locator,
        exercise: BasicExercise,
        *,
        attempt: str,
    ) -> None:
        if exercise.question_type == "input":
            answer = normalize_windows_path_escaping(exercise.answers[0])
            expected_input = (
                self._wrong_input_value(answer) if attempt == "incorrect" else answer
            )
            await self._ensure_input_answer_ready(
                card,
                exercise,
                expected_input,
                attempt=attempt,
            )

        send_button = card.locator("button#send-btn:visible").first
        await expect(send_button).to_be_enabled(timeout=settings.DOM_TIMEOUT_MS)
        await perform_and_wait_for_response(
            self.page,
            lambda: safe_click(send_button),
            predicate=lambda response: response_path_is(
                response,
                path="/api/basic/exercise/submit_basic/",
                method="POST",
            ),
            description=(
                f"Отправка {attempt}-ответа: шаг {exercise.step_id}, Q{exercise.order}"
            ),
        )

    async def _submit_correct_answer_with_retries(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        max_attempts = max(
            1,
            int(
                getattr(
                    settings,
                    "BASIC_CORRECT_ANSWER_MAX_ATTEMPTS",
                    3,
                )
            ),
        )
        quick_timeout_ms = int(
            getattr(
                settings,
                "BASIC_CORRECT_ANSWER_RETRY_TIMEOUT_MS",
                5_000,
            )
        )
        last_error: QuestionAnswerRejectedError | None = None

        for attempt_index in range(1, max_attempts + 1):
            await self._submit_current_answer(
                card,
                exercise,
                attempt=f"correct#{attempt_index}",
            )
            timeout_ms = (
                settings.BASIC_QUESTION_STATUS_TIMEOUT_MS
                if attempt_index == max_attempts
                else quick_timeout_ms
            )
            try:
                await self._verify_completed_question(
                    card,
                    exercise,
                    timeout_ms=timeout_ms,
                )
                if attempt_index > 1:
                    log_step(
                        f"[QUESTION][STEP {exercise.step_id}]"
                        f"[Q{exercise.order}] correct answer accepted "
                        f"after retry {attempt_index}"
                    )
                return
            except QuestionAnswerRejectedError as exc:
                last_error = exc
                if attempt_index == max_attempts:
                    break
                log_step(
                    f"[QUESTION][STEP {exercise.step_id}]"
                    f"[Q{exercise.order}] correct answer was not "
                    f"accepted on attempt {attempt_index}; retrying"
                )
                card = self.question_card(exercise.order)
                if await self._question_is_completed(card):
                    await self._verify_completed_question(
                        card,
                        exercise,
                        timeout_ms=quick_timeout_ms,
                    )
                    return
                await self._open_question_for_retry(card, exercise)
                await self._verify_question_text(card, exercise)
                await self._prepare_correct_answer(card, exercise)

        if last_error is not None:
            raise last_error
        raise AssertionError(f"Q{exercise.order}: correct answer was not submitted")

    async def _verify_incorrect_question(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        await card.evaluate(
            """
            (element) => new Promise((resolve, reject) => {
                const hasIncorrectStatus = () => {
                    const marker = (
                        element.className + " " + element.innerHTML
                    ).toLowerCase();
                    return /\\b(failed|error)\\b/.test(marker)
                        || marker.includes("forbidden.svg")
                        || marker.includes("success-yellow.svg")
                        || marker.includes("partial_success")
                        || marker.includes("partial-success");
                };
                if (hasIncorrectStatus()) {
                    resolve(true);
                    return;
                }
                const observer = new MutationObserver(() => {
                    if (hasIncorrectStatus()) {
                        observer.disconnect();
                        resolve(true);
                    }
                });
                observer.observe(element, {
                    attributes: true,
                    childList: true,
                    subtree: true
                });
                window.setTimeout(() => {
                    observer.disconnect();
                    reject(new Error("incorrect status timeout"));
                }, 5000);
            })
            """
        )
        if await self._question_is_completed(card):
            raise AssertionError(
                f"Q{exercise.order}: неверный ответ засчитан как completed"
            )

    async def _question_is_completed(self, card: Locator) -> bool:
        classes = (await card.get_attribute("class")) or ""
        return "completed" in classes.split()

    async def _verify_completed_question(
        self,
        card: Locator,
        exercise: BasicExercise,
        *,
        timeout_ms: int | None = None,
    ) -> None:
        effective_timeout_ms = (
            settings.BASIC_QUESTION_STATUS_TIMEOUT_MS
            if timeout_ms is None
            else timeout_ms
        )
        try:
            await self.page.wait_for_function(
                """
                ({ order }) => {
                    const visible = (element) => Boolean(
                        element
                        && getComputedStyle(element).visibility !== "hidden"
                        && getComputedStyle(element).display !== "none"
                        && element.getClientRects().length
                    );
                    const container = Array.from(
                        document.querySelectorAll(
                            ".scenarios-basic-result__tasks-container"
                        )
                    ).find(visible);
                    const element = container?.querySelector(
                        `#ex_${order}`
                    );
                    if (!visible(element)) return false;
                    const marker = (
                        `${element.className || ""} `
                        + `${element.innerHTML || ""}`
                    ).toLowerCase();
                    return marker.includes("success-filled.svg")
                        || /\\bcompleted\\b/.test(marker);
                }
                """,
                arg={"order": exercise.order},
                timeout=effective_timeout_ms,
            )
        except Exception as exc:
            current_card = self.question_card(exercise.order)
            marker = await current_card.evaluate(
                """
                (element) => (
                    `${element.className || ""} `
                    + `${element.innerHTML || ""}`
                ).toLowerCase()
                """
            )
            if "forbidden.svg" in marker or re.search(r"\b(failed|error)\b", marker):
                status = "failed"
            elif (
                "success-yellow.svg" in marker
                or "partial_success" in marker
                or "partial-success" in marker
            ):
                status = "partial_success"
            else:
                status = "pending"
            raise QuestionAnswerRejectedError(
                f"Q{exercise.order}: правильный ответ не засчитан, "
                f"после ожидания "
                f"{effective_timeout_ms} мс "
                f"статус карточки {status}"
            ) from exc

        card = self.question_card(exercise.order)
        await expect(card).to_have_class(
            re.compile(r"\bcompleted\b"),
            timeout=effective_timeout_ms,
        )
        await expect(
            card.locator("img.actionbar__step[src*='success-filled.svg']").first
        ).to_be_visible(timeout=effective_timeout_ms)
        await self._verify_question_text(card, exercise)

    async def _prepare_correct_answer(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        if exercise.question_type == "input":
            await self._fill_input_answer(card, exercise)
        elif exercise.question_type == "radio":
            await self._select_radio_answer(card, exercise)
        elif exercise.question_type == "checkbox":
            await self._select_checkbox_answers(card, exercise)
        elif exercise.question_type == "drag-n-drop":
            await self._arrange_drag_answers(card, exercise)
        else:
            raise AssertionError(
                f"Unsupported question type {exercise.question_type!r}"
            )

    async def _fill_input_answer(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        answer = self._normalized_input_answer(exercise.answers[0])
        field = self._input_answer_field(card)
        await expect(field).to_be_visible()
        await self._replace_text_field_value(field, answer)

    async def _fill_incorrect_input_answer(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        answer = self._normalized_input_answer(exercise.answers[0])
        wrong = self._wrong_input_value(answer)
        field = self._input_answer_field(card)
        await expect(field).to_be_visible()
        await self._replace_text_field_value(field, wrong)
        log_step(
            f"[QUESTION][STEP {exercise.step_id}][Q{exercise.order}] "
            f"incorrect input value: {wrong!r}"
        )

    @staticmethod
    def _normalized_input_answer(answer: str) -> str:
        normalized = normalize_windows_path_escaping(answer)
        if normalized != answer:
            log_step(
                "[QUESTION][INPUT] Нормализовано экранирование Windows-пути "
                f"из GitLab: символов={len(answer)}->{len(normalized)}, "
                f"разделителей={answer.count('\\')}->{normalized.count('\\')}"
            )
        return normalized

    async def _replace_text_field_value(
        self,
        field: Locator,
        value: str,
    ) -> None:
        """Атомарно заменяет ответ и синхронизирует Unicode с Angular."""

        if not value:
            raise QuestionAnswerRejectedError(
                "Нельзя подготовить пустой ответ для input-вопроса"
            )

        last_error: Exception | None = None
        last_diagnostics = "диагностика поля недоступна"
        input_attempt_timeout_ms = min(settings.DOM_TIMEOUT_MS, 2_000)
        for strategy in (
            "fill",
            "select_insert_text",
            "select_press_sequentially",
            "native_replacement",
        ):
            try:
                await expect(field).to_be_visible(
                    timeout=input_attempt_timeout_ms,
                )
                await expect(field).to_be_enabled(
                    timeout=input_attempt_timeout_ms,
                )
                if strategy != "native_replacement":
                    await field.scroll_into_view_if_needed()
                    await field.click(timeout=input_attempt_timeout_ms)
                    await field.focus()
                if strategy == "fill":
                    await field.fill(value)
                elif strategy == "select_insert_text":
                    await field.focus()
                    await field.select_text()
                    # Один CDP insertText передает всю Unicode-строку без
                    # зависимости от текущей раскладки Windows. Выделение
                    # обеспечивает замену без нестабильного пустого состояния.
                    await self.page.keyboard.insert_text(value)
                elif strategy == "select_press_sequentially":
                    await field.focus()
                    await field.select_text()
                    await field.press_sequentially(value, delay=60)
                else:
                    # Нативная подстановка — независимый от мыши fallback.
                    # Видимое Angular-поле может быть перекрыто невидимым
                    # overlay после подсказки, хотя оно connected/enabled.
                    # Требовать click здесь означало повторять одну и ту же
                    # ошибку во всех четырех стратегиях.
                    await field.evaluate(
                        """
                        (element, nextValue) => {
                            const prototype = element instanceof HTMLTextAreaElement
                                ? HTMLTextAreaElement.prototype
                                : HTMLInputElement.prototype;
                            const setter = Object.getOwnPropertyDescriptor(
                                prototype,
                                "value"
                            )?.set;
                            if (setter) setter.call(element, nextValue);
                            else element.value = nextValue;

                            const inputEvent = typeof InputEvent === "function"
                                ? new InputEvent("input", {
                                    bubbles: true,
                                    composed: true,
                                    inputType: "insertReplacementText",
                                    data: nextValue
                                })
                                : new Event("input", {
                                    bubbles: true,
                                    composed: true
                                });
                            element.dispatchEvent(inputEvent);
                            element.dispatchEvent(new Event("change", {
                                bubbles: true,
                                composed: true
                            }));
                        }
                        """,
                        value,
                    )

                await self._expect_stable_text_field_value(
                    field,
                    value,
                    timeout=input_attempt_timeout_ms,
                )
                await self._synchronize_text_field_model(field)
                await self._expect_stable_text_field_value(
                    field,
                    value,
                    timeout=input_attempt_timeout_ms,
                )
                actual_value = await field.input_value()
                if actual_value != value:
                    log_step(
                        "[QUESTION][INPUT] Web нормализовал завершающий "
                        "разделитель Windows-пути; отправляем принятое DOM-значение "
                        "и проверяем результат по серверному статусу карточки"
                    )
                if strategy != "fill":
                    log_step(
                        "[QUESTION][INPUT] Unicode answer entered with "
                        f"fallback strategy={strategy}"
                    )
                return
            except Exception as exc:
                last_error = exc
                last_diagnostics = await self._text_field_diagnostics(field)
                log_step(
                    "[QUESTION][INPUT] strategy="
                    f"{strategy} did not keep the expected value; "
                    f"{last_diagnostics}; error="
                    f"{compact_exception_message(exc)}"
                )

        raise QuestionAnswerRejectedError(
            "Не удалось атомарно заменить Unicode-ответ в input/password после "
            "стратегий fill, select_insert_text, "
            "select_press_sequentially и native_replacement; "
            "submit не выполнен: "
            f"{compact_exception_message(last_error)}; {last_diagnostics}"
        ) from last_error

    @staticmethod
    async def _expect_stable_text_field_value(
        field: Locator,
        expected_value: str,
        *,
        timeout: int,
    ) -> None:
        """Требует точное значение несколько циклов подряд после Angular render."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout / 1_000
        stable_since: float | None = None
        last_value = ""

        while loop.time() < deadline:
            try:
                last_value = await field.input_value(timeout=min(timeout, 500))
            except Exception:
                stable_since = None
            else:
                if BasicModulesPage._text_field_value_matches(
                    last_value,
                    expected_value,
                ):
                    if stable_since is None:
                        stable_since = loop.time()
                    elif loop.time() - stable_since >= 0.15:
                        return
                else:
                    stable_since = None
            await asyncio.sleep(0.03)

        raise AssertionError(
            "input value was not stable after Angular render: "
            f"expected_length={len(expected_value)}, "
            f"actual_length={len(last_value)}, "
            f"casefold_match="
            f"{last_value.casefold() == expected_value.casefold()}, "
            f"expected_is_upper={expected_value.isupper()}, "
            f"actual_is_upper={last_value.isupper()}"
        )

    @staticmethod
    def _text_field_value_matches(
        actual_value: str,
        expected_value: str,
    ) -> bool:
        """Учитывает единственную известную нормализацию Sandbox input."""

        if actual_value == expected_value:
            return True
        return bool(
            re.match(r"^[A-Za-z]:\\", expected_value)
            and expected_value.endswith("\\")
            and actual_value == expected_value[:-1]
        )

    @staticmethod
    async def _text_field_diagnostics(field: Locator) -> str:
        """Возвращает безопасные атрибуты поля без текста ответа."""

        try:
            details = await field.evaluate(
                """
                (element) => ({
                    tag: element.tagName?.toLowerCase() || "unknown",
                    type: element.getAttribute("type") || "text",
                    placeholder: element.getAttribute("placeholder") || "",
                    readonly: element.readOnly === true,
                    disabled: element.disabled === true,
                    connected: element.isConnected === true,
                    maxLength: element.maxLength,
                    maxLengthAttribute: element.getAttribute("maxlength"),
                    valueLength: `${element.value || ""}`.length,
                    selectionStart: element.selectionStart,
                    selectionEnd: element.selectionEnd
                })
                """
            )
        except Exception as exc:
            return "field diagnostics error=" + compact_exception_message(exc)

        if not isinstance(details, dict):
            return "field diagnostics returned no attributes"
        return ", ".join(f"{key}={value!r}" for key, value in details.items())

    @staticmethod
    async def _synchronize_text_field_model(field: Locator) -> None:
        await field.evaluate(
            """
            (element) => {
                const currentValue = element.value;
                const inputEvent = typeof InputEvent === "function"
                    ? new InputEvent("input", {
                        bubbles: true,
                        composed: true,
                        inputType: "insertReplacementText",
                        data: currentValue
                    })
                    : new Event("input", {
                        bubbles: true,
                        composed: true
                    });
                element.dispatchEvent(inputEvent);
                element.dispatchEvent(new Event("change", {
                    bubbles: true,
                    composed: true
                }));
            }
            """
        )

    async def _ensure_input_answer_ready(
        self,
        card: Locator,
        exercise: BasicExercise,
        expected_value: str,
        *,
        attempt: str,
    ) -> None:
        """Не допускает submit пустого или сброшенного Angular input."""

        field = self._input_answer_field(card)
        await expect(field).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        await expect(field).to_be_enabled(timeout=settings.DOM_TIMEOUT_MS)

        actual_value = await field.input_value()
        if not self._text_field_value_matches(actual_value, expected_value):
            log_step(
                f"[QUESTION][STEP {exercise.step_id}][Q{exercise.order}] "
                f"input value was reset before {attempt} submit; filling again"
            )
            await self._replace_text_field_value(
                field,
                expected_value,
            )
        else:
            await self._synchronize_text_field_model(field)

        try:
            await self._expect_stable_text_field_value(
                field,
                expected_value,
                timeout=settings.DOM_TIMEOUT_MS,
            )
            if not (await field.input_value()).strip():
                raise AssertionError("empty input value")
        except Exception as exc:
            raise QuestionAnswerRejectedError(
                f"Q{exercise.order}: input-ответ не удалось подготовить; "
                "пустой submit не выполнен"
            ) from exc
        log_step(
            f"[QUESTION][STEP {exercise.step_id}][Q{exercise.order}] "
            f"input value confirmed before {attempt} submit"
        )

    @staticmethod
    def _input_answer_field(card: Locator) -> Locator:
        return card.locator(
            "input:visible:not([type='checkbox']):not([type='radio'])"
            ":not([type='hidden']):not([type='button']):not([type='submit']), "
            "textarea:visible"
        ).first

    async def _select_radio_answer(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        components = card.locator("kbq-radio-button:visible, kbq-radio:visible")
        await self._verify_choice_options(
            components,
            exercise.options,
            exercise=exercise,
        )
        answer_component = await self._choice_component_by_text(
            components,
            exercise.answers[0],
        )
        input_locator = answer_component.locator("input[type='radio']").first
        await safe_click(
            answer_component.locator("label").first
            if await answer_component.locator("label").count()
            else answer_component
        )
        if await input_locator.count() > 0:
            await expect(input_locator).to_be_checked()

    async def _select_incorrect_radio_answer(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        wrong_answer = next(
            option
            for option in exercise.options
            if _normalize_text(option) != _normalize_text(exercise.answers[0])
        )
        components = card.locator("kbq-radio-button:visible, kbq-radio:visible")
        answer_component = await self._choice_component_by_text(
            components,
            wrong_answer,
        )
        input_locator = answer_component.locator("input[type='radio']").first
        await safe_click(
            answer_component.locator("label").first
            if await answer_component.locator("label").count()
            else answer_component
        )
        if await input_locator.count() > 0:
            await expect(input_locator).to_be_checked()
        log_step(
            f"[QUESTION][STEP {exercise.step_id}][Q{exercise.order}] "
            f"incorrect radio value: {wrong_answer!r}"
        )

    async def _select_checkbox_answers(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        await self._set_checkbox_answers(
            card,
            exercise,
            exercise.answers,
        )

    async def _set_checkbox_answers(
        self,
        card: Locator,
        exercise: BasicExercise,
        answers: tuple[str, ...],
    ) -> None:
        components = card.locator("kbq-checkbox:visible")
        await self._verify_choice_options(
            components,
            exercise.options,
            exercise=exercise,
        )
        expected = {_normalize_text(answer) for answer in answers}

        for index in range(await components.count()):
            component = components.nth(index)
            text = _normalize_text(await component.inner_text())
            checkbox = component.locator("input[type='checkbox']").first
            await expect(checkbox).to_be_attached()
            should_be_checked = text in expected
            is_checked = await checkbox.is_checked()
            if should_be_checked != is_checked:
                label = component.locator("label").first
                await safe_click(label if await label.count() else component)
            if should_be_checked:
                await expect(checkbox).to_be_checked()
            else:
                await expect(checkbox).not_to_be_checked()

    async def _select_incorrect_checkbox_answers(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        components = card.locator("kbq-checkbox:visible")
        expected = {_normalize_text(answer) for answer in exercise.answers}
        wrong_expected = set(expected)
        options: list[str] = []
        for index in range(await components.count()):
            options.append(_normalize_text(await components.nth(index).inner_text()))
        extra = next(
            (option for option in options if option not in expected),
            None,
        )
        if extra is not None:
            wrong_expected.add(extra)
        elif wrong_expected:
            wrong_expected.remove(next(iter(wrong_expected)))

        for index in range(await components.count()):
            component = components.nth(index)
            text = _normalize_text(await component.inner_text())
            checkbox = component.locator("input[type='checkbox']").first
            await expect(checkbox).to_be_attached()
            should_be_checked = text in wrong_expected
            if should_be_checked != await checkbox.is_checked():
                label = component.locator("label").first
                await safe_click(label if await label.count() else component)
            if should_be_checked:
                await expect(checkbox).to_be_checked()
            else:
                await expect(checkbox).not_to_be_checked()
        log_step(
            f"[QUESTION][STEP {exercise.step_id}][Q{exercise.order}] "
            "incorrect checkbox set selected"
        )

    def _wrong_input_value(self, answer: str) -> str:
        result: list[str] = []
        changed = False
        for char in answer:
            if char.isdecimal():
                result.append("1" if char != "1" else "2")
                changed = True
            elif char.isalpha():
                if "а" <= char.casefold() <= "я" or char.casefold() == "ё":
                    replacement = "я" if char.casefold() != "я" else "ю"
                else:
                    replacement = "x" if char.casefold() != "x" else "y"
                if char.isupper():
                    replacement = replacement.upper()
                elif char.islower():
                    replacement = replacement.lower()
                result.append(replacement)
                changed = True
            else:
                result.append(char)
        wrong = "".join(result)
        if changed and wrong != answer:
            return wrong
        fallback = "X" if answer.isupper() else "x"
        if answer == fallback:
            return "Y" if answer.isupper() else "y"
        return fallback

    async def _verify_choice_options(
        self,
        components: Locator,
        expected_options: tuple[str, ...],
        *,
        exercise: BasicExercise,
    ) -> None:
        await expect(components.first).to_be_visible()
        actual_options_list: list[str] = []
        for index in range(await components.count()):
            actual_options_list.append(
                _normalize_text(await components.nth(index).inner_text())
            )
        actual_options = tuple(actual_options_list)
        expected_normalized = tuple(
            _normalize_text(option) for option in expected_options
        )
        if actual_options != expected_normalized:
            raise QuestionContentMismatchError(
                f"Варианты ответа для шага {exercise.step_id}, "
                f"Q{exercise.order} не совпали с GitLab: "
                f"ожидалось={expected_options!r}, "
                f"получено={actual_options!r}"
            )

    async def _choice_component_by_text(
        self,
        components: Locator,
        answer: str,
    ) -> Locator:
        expected = _normalize_text(answer)
        for index in range(await components.count()):
            component = components.nth(index)
            if _normalize_text(await component.inner_text()) == expected:
                return component
        raise QuestionContentMismatchError(f"В DOM не найден вариант ответа «{answer}»")

    async def _arrange_drag_answers(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        desired_answers = tuple(answer for _, answer in exercise.answer_mapping)
        answer_list = await self._find_drag_answer_list(
            card,
            desired_answers,
        )
        await self._verify_drag_prompts(card, exercise)

        for target_index, desired_answer in enumerate(desired_answers):
            for attempt in range(1, 4):
                items = self._drag_items(answer_list)
                current = await self._locator_texts(items)
                current_normalized = [_normalize_text(text) for text in current]
                desired_normalized = _normalize_text(desired_answer)
                if (
                    target_index < len(current_normalized)
                    and current_normalized[target_index] == desired_normalized
                ):
                    break
                source_index = self._drag_source_index(
                    current_normalized,
                    desired_normalized=desired_normalized,
                    target_index=target_index,
                )
                await self._drag_item(
                    items,
                    source_index=source_index,
                    target_index=target_index,
                    strategy=attempt,
                )
                if await self._wait_for_drag_position(
                    answer_list,
                    target_index=target_index,
                    desired_answer=desired_answer,
                ):
                    await self._wait_for_drag_settled(answer_list)
                    break
                if attempt == 3:
                    raise AssertionError(
                        f"Не удалось переместить «{desired_answer}» "
                        f"в позицию {target_index + 1}"
                    )

        actual = tuple(
            _normalize_text(text)
            for text in await self._locator_texts(self._drag_items(answer_list))
        )
        expected = tuple(_normalize_text(text) for text in desired_answers)
        if actual != expected:
            raise AssertionError(
                f"Порядок drag-n-drop не совпал с GitLab: "
                f"ожидалось={desired_answers!r}, получено={actual!r}"
            )

    def _drag_source_index(
        self,
        current_answers: list[str],
        *,
        desired_normalized: str,
        target_index: int,
    ) -> int:
        """Выбирает дубль справа, не затрагивая готовый префикс."""

        for source_index in range(
            target_index + 1,
            len(current_answers),
        ):
            if current_answers[source_index] == desired_normalized:
                return source_index
        raise AssertionError(
            "Не найдено доступной плашки drag-n-drop "
            f"«{desired_normalized}» для позиции {target_index + 1}; "
            f"текущий порядок={tuple(current_answers)!r}"
        )

    async def _find_drag_answer_list(
        self,
        card: Locator,
        desired_answers: tuple[str, ...],
    ) -> Locator:
        expected = sorted(_normalize_text(answer) for answer in desired_answers)
        lists = card.locator("[cdkDropList], [cdkdroplist], .cdk-drop-list")
        for index in range(await lists.count()):
            candidate = lists.nth(index)
            items = self._drag_items(candidate)
            if await items.count() != len(desired_answers):
                continue
            actual = sorted(
                _normalize_text(text) for text in await self._locator_texts(items)
            )
            if actual == expected:
                return candidate
        raise QuestionContentMismatchError(
            f"В DOM не найден список drag-n-drop с вариантами {desired_answers!r}"
        )

    async def _verify_drag_prompts(
        self,
        card: Locator,
        exercise: BasicExercise,
    ) -> None:
        expected = {_normalize_text(option) for option in exercise.options}
        candidates = card.locator(
            ".cdk-drag-disabled, "
            "[cdkdrag][aria-disabled='true'], "
            ".basic-scenario-tasks-card-not-draggable-item"
        )
        actual = {
            _normalize_text(await candidates.nth(index).inner_text())
            for index in range(await candidates.count())
        }
        if not expected.issubset(actual):
            raise QuestionContentMismatchError(
                f"Строки drag-n-drop для шага {exercise.step_id}, "
                f"Q{exercise.order} не совпали с GitLab: "
                f"ожидалось={exercise.options!r}, получено={actual!r}"
            )

    def _drag_items(self, answer_list: Locator) -> Locator:
        return answer_list.locator(":scope > .basic-scenario-tasks-card-draggable-item")

    async def _locator_texts(self, locator: Locator) -> list[str]:
        return [
            await locator.nth(index).inner_text()
            for index in range(await locator.count())
        ]

    async def _wait_for_drag_position(
        self,
        answer_list: Locator,
        *,
        target_index: int,
        desired_answer: str,
    ) -> bool:
        desired = _normalize_text(desired_answer)
        for _ in range(12):
            items = self._drag_items(answer_list)
            texts = await self._locator_texts(items)
            if (
                target_index < len(texts)
                and _normalize_text(texts[target_index]) == desired
            ):
                return True
            await self.page.wait_for_timeout(100)
        return False

    async def _wait_for_drag_settled(
        self,
        answer_list: Locator,
    ) -> None:
        for _ in range(20):
            list_classes = ((await answer_list.get_attribute("class")) or "").split()
            items = self._drag_items(answer_list)
            item_is_animating = False
            for index in range(await items.count()):
                item_classes = (
                    (await items.nth(index).get_attribute("class")) or ""
                ).split()
                if "cdk-drag-animating" in item_classes:
                    item_is_animating = True
                    break

            if "cdk-drop-list-dragging" not in list_classes and not item_is_animating:
                return
            await self.page.wait_for_timeout(50)

    async def _drag_item(
        self,
        items: Locator,
        *,
        source_index: int,
        target_index: int,
        strategy: int,
    ) -> None:
        source = items.nth(source_index)
        target = items.nth(target_index)
        await source.scroll_into_view_if_needed()
        await target.scroll_into_view_if_needed()
        await source.hover(timeout=settings.DOM_TIMEOUT_MS)

        source_box = await source.bounding_box()
        target_box = await target.bounding_box()
        if source_box is None or target_box is None:
            raise AssertionError("Не удалось получить координаты drag-n-drop")

        source_x = source_box["x"] + source_box["width"] / 2
        source_y = source_box["y"] + source_box["height"] / 2
        target_height = target_box["height"]
        edge_offsets = (0.2, 0.1, 0.05)
        edge_offset = edge_offsets[min(max(strategy, 1), len(edge_offsets)) - 1]
        target_x = target_box["x"] + target_box["width"] / 2
        target_y_offset = (
            max(2, target_height * edge_offset)
            if source_index > target_index
            else min(
                target_height - 2,
                target_height * (1 - edge_offset),
            )
        )
        target_y = target_box["y"] + target_y_offset
        direction = 1 if target_y > source_y else -1
        activation_distance = min(
            max(10.0, source_box["height"] * 0.2),
            source_box["height"] * 0.4,
        )
        activation_y = source_y + direction * activation_distance
        travel_steps = 12 + min(max(strategy, 1), 3) * 4

        await source.evaluate(
            "(element) => element.ownerDocument.getSelection()?.removeAllRanges()"
        )
        await self.page.mouse.move(source_x, source_y)

        mouse_is_down = False
        try:
            await self.page.mouse.down(button="left")
            mouse_is_down = True
            await self.page.wait_for_timeout(75)
            await self.page.mouse.move(
                source_x,
                activation_y,
                steps=4,
            )
            await self.page.wait_for_timeout(50)
            await self.page.mouse.move(
                target_x,
                target_y,
                steps=travel_steps,
            )
            await self.page.wait_for_timeout(75)
        finally:
            if mouse_is_down:
                await self.page.mouse.up(button="left")

    async def _verify_product_link_contract(
        self,
        link: Locator | None = None,
    ) -> None:
        link = link if link is not None else self.product_link()
        await expect(link).to_be_visible()
        href = (await link.get_attribute("href")) or ""
        parsed = urlsplit(href)
        configured_host = getattr(settings, "PRODUCT_HOST", "").strip()
        if configured_host and parsed.hostname != configured_host:
            raise AssertionError(
                f"Ссылка {settings.PRODUCT_NAME} ведет на неверный узел: {href}"
            )
        if (
            not parsed.hostname
            or parsed.hostname == urlsplit(settings.BASE_URL).hostname
        ):
            raise AssertionError(
                f"Ссылка {settings.PRODUCT_NAME} не ведет во внешний продукт: {href}"
            )
        if not parsed.fragment and not parsed.query:
            raise AssertionError(
                f"В ссылке {settings.PRODUCT_NAME} отсутствует продуктовый "
                f"маршрут: {href}"
            )
        log_step(f"[PRODUCT] Ссылка {settings.PRODUCT_NAME} подтверждена: {href}")

    async def _open_product_page(
        self,
        link: Locator | None = None,
    ) -> Page:
        link = link if link is not None else self.product_link()
        async with self.page.context.expect_page(
            timeout=settings.DOM_TIMEOUT_MS
        ) as page_info:
            await safe_click(link)
        product_page = await page_info.value
        product_page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
        product_page.set_default_navigation_timeout(
            getattr(
                settings,
                "PRODUCT_LOAD_TIMEOUT_MS",
                settings.DEFAULT_TIMEOUT_MS,
            )
        )
        try:
            await product_page.wait_for_load_state(
                "domcontentloaded",
                timeout=getattr(
                    settings,
                    "PRODUCT_LOAD_TIMEOUT_MS",
                    settings.DOM_TIMEOUT_MS,
                ),
            )
        except Exception:
            pass
        await product_page.bring_to_front()
        await maximize_page_window(product_page)
        await self._bypass_product_security_warnings(product_page)
        log_step(
            f"[PRODUCT] Вкладка {settings.PRODUCT_NAME} открыта: {product_page.url}"
        )
        return product_page

    async def _login_product_if_needed(
        self,
        product_page: Page,
        *,
        username: str,
        password: str,
        wait_for_app_after_submit: bool = True,
    ) -> None:
        username_input = product_page.locator(
            "input#usernameLocal, "
            "input[data-testid='platform_inpLocalUsername'], "
            "input[name='username'], "
            "input#login, "
            "input[formcontrolname='username'], "
            "input[autocomplete='username']"
        ).first
        await self._close_product_release_notes_if_present(product_page)
        product_app = product_page.locator(
            ".mc-action-bar__left:visible, #timeRange:visible, "
            ".ag-header-cell[col-id='time'] "
            "date-time-range-filter .filter-popover:visible"
        ).first
        if (
            await product_app.is_visible()
            or await self._product_app_scope(product_page) is not None
        ):
            log_step(
                "[PRODUCT] Форма входа не показана; "
                f"сессия {settings.PRODUCT_NAME} уже активна"
            )
            return

        try:
            await username_input.wait_for(state="visible", timeout=8_000)
        except Exception:
            await self._close_product_release_notes_if_present(
                product_page,
                timeout_ms=1_000,
            )
            if (
                await product_app.is_visible()
                or await self._product_app_scope(product_page) is not None
            ):
                log_step(
                    "[PRODUCT] Форма входа не показана; "
                    f"сессия {settings.PRODUCT_NAME} уже активна"
                )
                return
            try:
                await product_page.locator(
                    ".mc-action-bar__left:visible, "
                    "#timeRange:visible, "
                    "#timeRange .time-range_link:visible, "
                    "time-range #timeRange:visible, "
                    ".ag-header-cell[col-id='time'] "
                    "date-time-range-filter .filter-popover:visible, "
                    "input#usernameLocal:visible, "
                    "input[data-testid='platform_inpLocalUsername']:visible, "
                    "input[name='username']:visible, "
                    "input#login:visible, "
                    "input[formcontrolname='username']:visible, "
                    "input[autocomplete='username']:visible"
                ).first.wait_for(
                    state="visible",
                    timeout=getattr(
                        settings,
                        "PRODUCT_LOAD_TIMEOUT_MS",
                        settings.DEFAULT_TIMEOUT_MS,
                    ),
                )
            except Exception:
                pass
            await self._close_product_release_notes_if_present(
                product_page,
                timeout_ms=1_000,
            )
            if (
                await product_app.is_visible()
                or await self._product_app_scope(product_page) is not None
            ):
                log_step(
                    "[PRODUCT] Форма входа не показана; "
                    f"сессия {settings.PRODUCT_NAME} уже активна"
                )
                return
            await username_input.wait_for(state="visible", timeout=1_000)

        password_input = product_page.locator(
            "input[data-testid='platform_inpPassword'], "
            "input[formcontrolname='password'], "
            "input#password, "
            "input[type='password']"
        ).first
        await username_input.fill(username)
        await password_input.fill(password)

        remember = (
            product_page.locator("label.kbq-checkbox__layout:visible")
            .filter(has_text=re.compile(r"Запомнить меня", re.IGNORECASE))
            .first
        )
        if await remember.count() > 0:
            checkbox = remember.locator("input[type='checkbox']").first
            if await checkbox.count() == 0 or not await checkbox.is_checked():
                await remember.click(force=True)

        login_button = product_page.locator(
            "button[data-testid='platform_btnLocalLogin']:visible"
        ).first
        if await login_button.count() == 0:
            login_button = product_page.get_by_role(
                "button",
                name=re.compile(r"^\s*Войти\s*$", re.IGNORECASE),
            ).first

        await perform_and_wait_for_response(
            product_page,
            lambda: login_button.click(force=True),
            predicate=response_is_mutation,
            description=f"Авторизация в {settings.PRODUCT_NAME}",
            timeout_ms=settings.NETWORK_TIMEOUT_MS,
        )
        await expect(username_input).to_be_hidden(
            timeout=settings.LOGIN_FORM_HIDE_TIMEOUT_MS
        )
        if not wait_for_app_after_submit:
            log_step(
                f"[PRODUCT] Выполнен вход в {settings.PRODUCT_NAME} как {username}; "
                "готовность приложения будет проверена при повторном открытии"
            )
            return
        await self._bypass_product_security_warnings(product_page)
        log_step(f"[PRODUCT] Выполнен вход в {settings.PRODUCT_NAME} как {username}")
        await self._close_product_release_notes_if_present(
            product_page,
            timeout_ms=5_000,
        )

    async def _close_product_release_notes_if_present(
        self,
        product_page: Page,
        *,
        timeout_ms: int = 1_500,
    ) -> bool:
        modal = product_page.locator(
            "ipn-release-notes-modal:visible, .release-notes-modal:visible"
        ).first
        try:
            await expect(modal).to_be_visible(timeout=timeout_ms)
        except Exception:
            return False

        close_button = product_page.locator(
            ".mc-modal-content:has(.release-notes-modal) "
            "button.mc-modal-close:visible, "
            ".cdk-overlay-pane:has(.release-notes-modal) "
            "button.mc-modal-close:visible, "
            "button.mc-modal-close:has(i[mc-icon='mc-close-L_16']):visible, "
            "button:has(i[mc-icon='mc-close-L_16']):visible"
        ).first
        try:
            await close_button.click(force=True, timeout=settings.DOM_TIMEOUT_MS)
        except Exception:
            clicked = await product_page.evaluate(
                """
                () => {
                    const visible = (element) => Boolean(
                        element
                        && getComputedStyle(element).visibility !== "hidden"
                        && getComputedStyle(element).display !== "none"
                        && element.getClientRects().length
                    );
                    const modal = document.querySelector(
                        ".release-notes-modal"
                    );
                    if (!visible(modal)) return false;

                    const root = modal.closest(".mc-modal-content")
                        || modal.closest(".cdk-overlay-pane")
                        || document;
                    const icon = root.querySelector(
                        "i[mc-icon='mc-close-L_16'], "
                        + ".mc-close-L_16, "
                        + "button.mc-modal-close"
                    );
                    if (!visible(icon)) return false;
                    const target = icon.closest("button,[role='button']")
                        || icon.closest(".mc-button-wrapper")
                        || icon;
                    target.click();
                    return true;
                }
                """
            )
            if not clicked:
                await product_page.keyboard.press("Escape")

        await expect(modal).to_be_hidden(timeout=settings.DOM_TIMEOUT_MS)
        log_step("[PRODUCT] Release notes modal closed")
        return True

    async def _bypass_product_security_warnings(
        self,
        product_page: Page,
    ) -> None:
        loop = asyncio.get_running_loop()
        readiness_deadline = None
        if settings.PRODUCT_INTERVAL_KIND == "nad":
            readiness_timeout_ms = getattr(
                settings,
                "PRODUCT_READINESS_TIMEOUT_MS",
                getattr(settings, "PRODUCT_LOAD_TIMEOUT_MS", 90_000),
            )
            readiness_deadline = loop.time() + readiness_timeout_ms / 1_000
        next_wait_log_at = loop.time() + 30
        warning_count = 0

        while True:
            if await self._close_product_release_notes_if_present(
                product_page,
                timeout_ms=300,
            ):
                continue

            if await self._product_page_ready(product_page):
                log_step(
                    f"[PRODUCT] {settings.PRODUCT_NAME} готов: "
                    f"обработано предупреждений={warning_count}"
                )
                return

            now = loop.time()
            if readiness_deadline is not None and now >= readiness_deadline:
                diagnostics = await self._product_readiness_diagnostics(product_page)
                raise ProductUnavailableError(
                    f"{settings.PRODUCT_NAME} не отдал рабочий DOM/маршрут "
                    f"за {readiness_timeout_ms} мс; "
                    f"обработано предупреждений={warning_count}; "
                    f"DOM={diagnostics}"
                )
            if now >= next_wait_log_at:
                diagnostics = await self._product_readiness_diagnostics(product_page)
                wait_message = (
                    "продолжаем DOM-ожидание без таймаута"
                    if readiness_deadline is None
                    else "продолжаем DOM-ожидание до ограниченного таймаута"
                )
                log_step(
                    f"[PRODUCT] {settings.PRODUCT_NAME} ещё загружается; "
                    f"{wait_message}: "
                    f"предупреждений={warning_count}, DOM={diagnostics}"
                )
                next_wait_log_at = now + 30

            if not await self._security_warning_visible(
                product_page,
                timeout_ms=1_000,
            ):
                continue

            warning_count += 1

            details = product_page.locator(
                "#details-button, "
                "button:has-text('Дополнительные настройки'), "
                "button:has-text('Advanced')"
            ).first
            proceed = product_page.locator(
                "#proceed-link, "
                "a:has-text('Перейти на сайт'), "
                "button:has-text('Перейти на сайт'), "
                "a:has-text('Proceed to'), "
                "button:has-text('Proceed to')"
            ).first
            try:
                if await details.is_visible():
                    await details.click(force=True)
                await expect(proceed).to_be_visible(timeout=5_000)
                await proceed.click(force=True)
            except Exception:
                if await self._product_page_ready(product_page):
                    return
                continue
            try:
                await product_page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=settings.DOM_TIMEOUT_MS,
                )
            except Exception:
                pass
            await product_page.bring_to_front()
            await maximize_page_window(product_page)
            log_step(
                f"[PRODUCT] Предупреждение безопасности пройдено ({warning_count})"
            )

    async def _product_readiness_diagnostics(
        self,
        product_page: Page,
    ) -> dict[str, object]:
        if product_page.is_closed():
            return {"closed": True}
        try:
            scopes: list[dict[str, object]] = []
            for index, scope in enumerate(self._product_dom_scopes(product_page)):
                time_range_labels = scope.locator("#timeRange .mc-link__text")
                scopes.append(
                    {
                        "index": index,
                        "kind": ("page" if isinstance(scope, Page) else "frame"),
                        "actionBars": await scope.locator(
                            ".mc-action-bar__left"
                        ).count(),
                        "timeRanges": await scope.locator("#timeRange").count(),
                        "timeRangeLabels": (
                            await time_range_labels.all_text_contents()
                        ),
                        "nadHeaders": await scope.locator(
                            "header nf-header mc-navbar"
                        ).count(),
                        "nadBrands": await scope.locator(
                            "header nf-header mc-navbar mc-navbar-brand mc-navbar-title"
                        ).all_text_contents(),
                        "nadDashboardLinks": await scope.locator(
                            "header nf-header mc-navbar a#header-menu-item-dashboards"
                        ).count(),
                        "loginInputs": await scope.locator(
                            "input#usernameLocal, "
                            "input[data-testid="
                            "'platform_inpLocalUsername'], "
                            "input[name='username'], "
                            "input[type='password']"
                        ).count(),
                    }
                )
            return {
                "closed": False,
                "readyState": await product_page.evaluate("document.readyState"),
                "url": product_page.url,
                "nadUrlInterval": str(
                    self._read_nad_interval_from_url(product_page.url)
                ),
                "nadDashboardReady": (
                    await self._nad_dashboard_scope(product_page) is not None
                ),
                "frames": len(product_page.frames),
                "releaseNotes": await product_page.locator(
                    "ipn-release-notes-modal, .release-notes-modal"
                ).count(),
                "scopes": scopes,
            }
        except Exception as exc:
            return {"evaluationError": type(exc).__name__}

    async def _product_page_ready(
        self,
        product_page: Page,
    ) -> bool:
        if product_page.is_closed():
            raise ProductUnavailableError(
                f"Вкладка {settings.PRODUCT_NAME} закрылась до загрузки"
            )
        try:
            if (
                settings.PRODUCT_INTERVAL_KIND == "nad"
                and await self._nad_dashboard_scope(product_page) is not None
            ):
                return True
            ready_selector = (
                "input#usernameLocal:visible, "
                "input[data-testid='platform_inpLocalUsername']:visible, "
                "input[name='username']:visible, "
                "input#login:visible, "
                "input[formcontrolname='username']:visible, "
                "input[autocomplete='username']:visible, "
                "input[type='password']:visible, "
                ".mc-action-bar__left:visible, "
                "#timeRange:visible, "
                "#timeRange .time-range_link:visible, "
                "time-range #timeRange:visible, "
                ".ag-header-cell[col-id='time'] "
                "date-time-range-filter .filter-popover:visible"
            )
            return (
                await self._find_visible_product_scope(
                    product_page,
                    ready_selector,
                )
                is not None
            )
        except Exception:
            return False

    def _product_dom_scopes(
        self,
        product_page: Page,
    ) -> tuple[ProductDomScope, ...]:
        child_frames = tuple(
            frame for frame in product_page.frames if frame != product_page.main_frame
        )
        return (product_page, *child_frames)

    async def _find_visible_product_scope(
        self,
        product_page: Page,
        selector: str,
    ) -> ProductDomScope | None:
        for scope in self._product_dom_scopes(product_page):
            try:
                if await scope.locator(selector).count() > 0:
                    return scope
            except Exception:
                continue
        return None

    async def _product_app_scope(
        self,
        product_page: Page,
    ) -> ProductDomScope | None:
        if settings.PRODUCT_INTERVAL_KIND == "nad":
            nad_scope = await self._nad_dashboard_scope(product_page)
            if nad_scope is not None:
                return nad_scope
        return await self._find_visible_product_scope(
            product_page,
            ".mc-action-bar__left:visible, "
            "#timeRange:visible, "
            "#timeRange .time-range_link:visible, "
            "time-range #timeRange:visible, "
            ".ag-header-cell[col-id='time'] "
            "date-time-range-filter .filter-popover:visible",
        )

    async def _nad_dashboard_scope(
        self,
        product_page: Page,
    ) -> ProductDomScope | None:
        if self._read_nad_interval_from_url(product_page.url) is None:
            return None
        try:
            if await product_page.evaluate("document.readyState") != "complete":
                return None
        except Exception:
            return None

        for scope in self._product_dom_scopes(product_page):
            try:
                navbar = scope.locator(
                    "header:visible nf-header mc-navbar:visible"
                ).first
                dashboard = navbar.locator(
                    "a#header-menu-item-dashboards:visible"
                ).first
                dashboard_title = (
                    dashboard.locator("mc-navbar-title")
                    .filter(has_text=re.compile(r"^\s*Дашборды\s*$", re.IGNORECASE))
                    .first
                )
                if (
                    await navbar.is_visible()
                    and await dashboard.is_visible()
                    and await dashboard_title.is_visible()
                ):
                    return scope
            except Exception:
                continue
        return None

    async def _wait_for_siem_product_scope(
        self,
        product_page: Page,
    ) -> ProductDomScope:
        selector = (
            ".mc-action-bar__left #timeRange:visible, "
            "#timeRange .time-range_link:visible, "
            "#timeRange .mc-link__text:visible, "
            "time-range #timeRange:visible"
        )
        while True:
            if product_page.is_closed():
                raise ProductUnavailableError(
                    "Вкладка MaxPatrol SIEM закрылась до появления DOM-кнопки интервала"
                )
            scope = await self._find_visible_product_scope(
                product_page,
                selector,
            )
            if scope is not None:
                scope_name = (
                    "основная страница"
                    if isinstance(scope, Page)
                    else "вложенный frame"
                )
                log_step(f"[PRODUCT] DOM интервала SIEM найден: {scope_name}")
                return scope
            await product_page.wait_for_timeout(250)

    async def _security_warning_visible(
        self,
        product_page: Page,
        *,
        timeout_ms: int,
    ) -> bool:
        try:
            await product_page.wait_for_function(
                """
                () => {
                    const visible = (element) => Boolean(
                        element && (
                            element.offsetWidth
                            || element.offsetHeight
                            || element.getClientRects().length
                        )
                    );
                    const bodyText = document.body?.innerText || "";
                    return visible(
                        document.querySelector("#details-button")
                    ) || visible(
                        document.querySelector("#proceed-link")
                    ) || /Подключение не защищено|Your connection isn't private|Your connection is not private/i
                        .test(bodyText);
                }
                """,
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    async def _read_matrix_solution_interval(
        self,
    ) -> ProductInterval:
        interval_locator = self.page.get_by_text(
            re.compile(
                r"Временной интервал.*\d{2}\.\d{2}\.\d{4}",
                re.IGNORECASE,
            )
        ).first
        await expect(interval_locator).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        text = await interval_locator.inner_text()
        pattern = re.compile(
            r"(\d{2}\.\d{2}\.\d{4})\s+"
            r"(\d{2}:\d{2})(?::\d{2})?\s*-\s*"
            r"(\d{2}\.\d{2}\.\d{4})\s+"
            r"(\d{2}:\d{2})(?::\d{2})?"
        )
        match = pattern.search(text)
        if match is None:
            raise AssertionError(f"Не удалось разобрать интервал Matrix: {text!r}")
        return ProductInterval(
            start_date=match.group(1),
            start_time=match.group(2),
            end_date=match.group(3),
            end_time=match.group(4),
        )

    async def _read_nad_interval_fields(
        self,
        product_page: Page,
    ) -> ProductInterval:
        url_interval = self._read_nad_interval_from_url(product_page.url)
        try:
            await self._expand_nad_interval_menu(product_page)
            from_period = product_page.locator(
                "nf-user-date-period[formcontrolname='from']"
            )
            to_period = product_page.locator(
                "nf-user-date-period[formcontrolname='to']"
            )
            start_date = from_period.locator("input.mc-datepicker:visible").first
            start_time = from_period.locator("input.mc-timepicker:visible").first
            end_date = to_period.locator("input.mc-datepicker:visible").first
            end_time = to_period.locator("input.mc-timepicker:visible").first
            for field in (
                start_date,
                start_time,
                end_date,
                end_time,
            ):
                await expect(field).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

            interval = ProductInterval(
                start_date=await self._field_value(start_date),
                start_time=await self._field_value(start_time),
                end_date=await self._field_value(end_date),
                end_time=await self._field_value(end_time),
            )
            log_step(
                f"[PRODUCT][NAD] Интервал прочитан из DOM-полей поповера: {interval}"
            )
            return interval
        except Exception as dom_error:
            if url_interval is None:
                raise AssertionError(
                    "Не удалось прочитать интервал PT NAD ни из DOM-полей, "
                    "ни из параметров from/to маршрута PT NAD"
                ) from dom_error
            log_step(
                "[PRODUCT][NAD] DOM-поля интервала недоступны; "
                f"используем from/to из URL маршрута: {url_interval}"
            )
            return url_interval

    @staticmethod
    def _read_nad_interval_from_url(url: str) -> ProductInterval | None:
        parsed = urlsplit(url)
        fragment = urlsplit(parsed.fragment)
        route = fragment.path.casefold().strip("/")
        if not route or route.startswith(("login", "connect/", "authorize")):
            return None
        query = parse_qs(fragment.query or parsed.query)
        try:
            start_ms = int(query["from"][0])
            end_ms = int(query["to"][0])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        if start_ms <= 0 or end_ms <= start_ms:
            return None

        start = datetime.fromtimestamp(start_ms / 1_000)
        end = datetime.fromtimestamp(end_ms / 1_000)
        return ProductInterval(
            start_date=start.strftime("%d.%m.%Y"),
            start_time=start.strftime("%H:%M"),
            end_date=end.strftime("%d.%m.%Y"),
            end_time=end.strftime("%H:%M"),
        )

    async def _expand_nad_interval_menu(
        self,
        product_page: Page,
    ) -> None:
        visible_field = product_page.locator(
            "nf-user-date-period[formcontrolname='from'] input.mc-datepicker:visible"
        ).first
        if await visible_field.count() > 0:
            return

        buttons = (
            product_page.locator(
                "button.nf-date-period-popover__item"
                "[mcpopover][mctrigger='click']:visible"
            ).first,
            product_page.locator("button[mcpopover][mctrigger='click']:visible")
            .filter(has_text=re.compile(r"\d{1,2}.*\d{2}:\d{2}"))
            .first,
            product_page.locator(".mc-button-wrapper:visible")
            .filter(has_text=re.compile(r"\d{1,2}.*\d{2}:\d{2}"))
            .locator("xpath=ancestor::button[1]")
            .first,
        )
        for button in buttons:
            try:
                await button.wait_for(
                    state="visible",
                    timeout=2_000,
                )
                await button.click(force=True)
                await expect(visible_field).to_be_visible(timeout=5_000)
                return
            except Exception:
                continue
        raise AssertionError("Не удалось раскрыть DOM-поля интервала PT NAD")

    async def _read_siem_interval_fields(
        self,
        product_page: Page,
        *,
        expected_interval: ProductInterval,
    ) -> ProductInterval:
        product_scope: ProductDomScope = product_page
        try:
            await self._close_product_release_notes_if_present(
                product_page,
                timeout_ms=1_000,
            )
            product_scope = await self._wait_for_siem_product_scope(product_page)
            await self._expand_siem_interval_menu(product_scope)
            fields = {
                "start_time": product_scope.locator(
                    "input[name='startTime']:visible"
                ).first,
                "start_date": product_scope.locator(
                    "input[name='startDate']:visible"
                ).first,
                "end_time": product_scope.locator(
                    "input[name='endTime']:visible"
                ).first,
                "end_date": product_scope.locator(
                    "input[name='endDate']:visible"
                ).first,
            }
            for field in fields.values():
                await expect(field).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
            return ProductInterval(
                start_date=await self._field_value(fields["start_date"]),
                start_time=await self._field_value(fields["start_time"]),
                end_date=await self._field_value(fields["end_date"]),
                end_time=await self._field_value(fields["end_time"]),
            )
        except Exception as fields_error:
            label_interval = await self._read_siem_interval_label(
                product_scope,
                expected_interval=expected_interval,
            )
            if label_interval is not None:
                return label_interval
            raise AssertionError(
                "Не удалось прочитать интервал MaxPatrol SIEM "
                "ни из полей периода, ни из подписи кнопки"
            ) from fields_error

    async def _read_sandbox_interval_fields(
        self,
        product_page: Page,
    ) -> ProductInterval:
        await self._close_product_release_notes_if_present(
            product_page,
            timeout_ms=1_000,
        )
        product_scope = await self._wait_for_sandbox_product_scope(product_page)
        popover = await self._expand_sandbox_interval_menu(product_scope)

        fields = {
            "start_date": popover.locator("input[name='startDate']:visible").first,
            "start_time": popover.locator("input[name='startDateTime']:visible").first,
            "end_date": popover.locator("input[name='endDate']:visible").first,
            "end_time": popover.locator("input[name='endDateTime']:visible").first,
        }
        for field in fields.values():
            await expect(field).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

        interval = ProductInterval(
            start_date=await self._field_value(fields["start_date"]),
            start_time=await self._field_value(fields["start_time"]),
            end_date=await self._field_value(fields["end_date"]),
            end_time=await self._field_value(fields["end_time"]),
        )
        await product_page.keyboard.press("Escape")
        return interval

    async def _wait_for_sandbox_product_scope(
        self,
        product_page: Page,
    ) -> ProductDomScope:
        selector = (
            ".ag-header-cell[col-id='time']:visible "
            "date-time-range-filter .filter-popover:visible"
        )
        while True:
            if product_page.is_closed():
                raise ProductUnavailableError(
                    "Вкладка PT Sandbox закрылась до появления фильтра интервала"
                )
            scope = await self._find_visible_product_scope(product_page, selector)
            if scope is not None:
                scope_name = (
                    "основная страница"
                    if isinstance(scope, Page)
                    else "вложенный frame"
                )
                log_step(
                    f"[PRODUCT] DOM фильтра интервала PT Sandbox найден: {scope_name}"
                )
                return scope
            await product_page.wait_for_timeout(250)

    async def _expand_sandbox_interval_menu(
        self,
        product_scope: ProductDomScope,
    ) -> Locator:
        fields_selector = (
            ".kbq-popover__container:visible input[name='startDate']:visible"
        )
        visible_fields = product_scope.locator(fields_selector).first
        if await visible_fields.count() > 0:
            return self._sandbox_interval_popover(product_scope)

        trigger = product_scope.locator(
            ".ag-header-cell[col-id='time']:visible "
            "date-time-range-filter .filter-popover:visible"
        ).first
        await expect(trigger).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        await expect(trigger.get_by_text("Дата создания", exact=True)).to_be_visible(
            timeout=settings.DOM_TIMEOUT_MS
        )
        trigger_text = _normalize_text((await trigger.text_content()) or "")
        log_step(
            "[PRODUCT] Нажимаем DOM-фильтр PT Sandbox "
            f"«Дата создания»: {trigger_text!r}"
        )

        for attempt in range(1, 4):
            if attempt == 1:
                await trigger.click(force=True, timeout=settings.DOM_TIMEOUT_MS)
            elif attempt == 2:
                await trigger.focus()
                await trigger.press("Enter")
            else:
                await trigger.evaluate(
                    """
                    (element) => element.dispatchEvent(new MouseEvent("click", {
                        bubbles: true,
                        cancelable: true,
                        view: window,
                    }))
                    """
                )
            try:
                await expect(visible_fields).to_be_visible(timeout=5_000)
                popover = self._sandbox_interval_popover(product_scope)
                range_radio = popover.locator("#radioPeriodRange:visible").first
                await expect(range_radio).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
                await expect(range_radio).to_have_class(
                    re.compile(r"\bkbq-selected\b"),
                    timeout=settings.DOM_TIMEOUT_MS,
                )
                log_step(
                    "[PRODUCT] Фильтр PT Sandbox раскрыт; выбран «Произвольный период»"
                )
                return popover
            except Exception:
                if attempt < 3:
                    await trigger.press("Escape")

        raise AssertionError(
            "Не удалось раскрыть поля произвольного периода PT Sandbox"
        )

    @staticmethod
    def _sandbox_interval_popover(product_scope: ProductDomScope) -> Locator:
        return product_scope.locator(
            ".kbq-popover__container:visible:has(input[name='startDate'])"
        ).first

    async def _expand_siem_interval_menu(
        self,
        product_scope: ProductDomScope,
    ) -> None:
        if await self._wait_for_siem_interval_fields(
            product_scope,
            timeout_ms=250,
        ):
            return
        time_range_button = product_scope.locator(
            "#timeRange > "
            "span.mc-link.time-range_link[mc-popover='popover']"
            "[template-url='timeRangePopover.html']:visible"
        ).first
        await time_range_button.wait_for(
            state="visible",
            timeout=settings.DOM_TIMEOUT_MS,
        )
        await expect(time_range_button.locator(".mc-link__text").first).to_contain_text(
            re.compile(r"за\s+\d", re.IGNORECASE),
            timeout=settings.DOM_TIMEOUT_MS,
        )
        button_text = re.sub(
            r"\s+",
            " ",
            ((await time_range_button.text_content()) or "").replace("\xa0", " "),
        ).strip()
        log_step(
            f"[PRODUCT] Нажимаем точную DOM-кнопку интервала SIEM: {button_text!r}"
        )
        await time_range_button.click(
            force=True,
            timeout=settings.DOM_TIMEOUT_MS,
        )
        if await self._wait_for_siem_interval_fields(
            product_scope,
            timeout_ms=settings.DOM_TIMEOUT_MS,
        ):
            log_step("[PRODUCT] Меню интервала SIEM раскрыто")
            return

        await time_range_button.evaluate(
            """
            (element) => element.dispatchEvent(new MouseEvent("click", {
                bubbles: true,
                cancelable: true,
                view: window,
            }))
            """
        )
        if await self._wait_for_siem_interval_fields(
            product_scope,
            timeout_ms=settings.DOM_TIMEOUT_MS,
        ):
            log_step("[PRODUCT] Меню интервала SIEM раскрыто через DOM-event fallback")
            return

        diagnostics = await product_scope.evaluate(
            """
            () => ({
                url: location.href,
                actionBar: Boolean(
                    document.querySelector(".mc-action-bar__left")
                ),
                timeRange: Boolean(document.querySelector("#timeRange")),
                timeRangeText: Array.from(
                    document.querySelectorAll(
                        "#timeRange .mc-link__text"
                    )
                ).map((element) => (element.textContent || "").trim()),
            })
            """
        )
        raise AssertionError(
            f"Не удалось раскрыть DOM-поля интервала MaxPatrol SIEM; DOM={diagnostics}"
        )

    async def _wait_for_siem_interval_fields(
        self,
        product_scope: ProductDomScope,
        *,
        timeout_ms: int = 5_000,
    ) -> bool:
        try:
            await expect(
                product_scope.locator("input[name='startTime']:visible").first
            ).to_be_visible(timeout=timeout_ms)
            await expect(
                product_scope.locator("input[name='endDate']:visible").first
            ).to_be_visible(timeout=timeout_ms)
            return True
        except Exception:
            return False

    async def _read_siem_interval_label(
        self,
        product_scope: ProductDomScope,
        *,
        expected_interval: ProductInterval,
    ) -> ProductInterval | None:
        months = (
            "января|янв|февраля|фев|марта|мар|апреля|апр|мая|"
            "июня|июн|июля|июл|августа|авг|сентября|сен|"
            "октября|окт|ноября|ноя|декабря|дек"
        )
        label = (
            product_scope.locator("button:visible .mc-button-wrapper")
            .filter(
                has_text=re.compile(
                    rf"(?:{months})\.?\s*.*\d{{2}}:\d{{2}}",
                    re.IGNORECASE,
                )
            )
            .first
        )
        try:
            await expect(label).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
            text = (await label.text_content()) or ""
        except Exception:
            return None

        normalized = re.sub(
            r"\s+",
            " ",
            text.replace("\xa0", " ").replace("\u2013", "-").replace("\u2014", "-"),
        ).strip()
        match = re.search(
            r"(\d{1,2})\s+([а-яё]+)\.?,\s*"
            r"(\d{2}:\d{2})(?::\d{2})?\s*-\s*"
            r"(\d{1,2})\s+([а-яё]+)\.?,\s*"
            r"(\d{2}:\d{2})(?::\d{2})?",
            normalized,
            re.IGNORECASE,
        )
        if match is None:
            return None
        start_month = self._russian_month_number(match.group(2))
        end_month = self._russian_month_number(match.group(5))
        if start_month is None or end_month is None:
            return None
        start_year = expected_interval.start_date.rsplit(".", 1)[-1]
        end_year = expected_interval.end_date.rsplit(".", 1)[-1]
        return ProductInterval(
            start_date=(f"{int(match.group(1)):02}.{start_month}.{start_year}"),
            start_time=match.group(3),
            end_date=(f"{int(match.group(4)):02}.{end_month}.{end_year}"),
            end_time=match.group(6),
        )

    @staticmethod
    def _russian_month_number(month_name: str) -> str | None:
        return {
            "января": "01",
            "янв": "01",
            "февраля": "02",
            "фев": "02",
            "марта": "03",
            "мар": "03",
            "апреля": "04",
            "апр": "04",
            "мая": "05",
            "июня": "06",
            "июн": "06",
            "июля": "07",
            "июл": "07",
            "августа": "08",
            "авг": "08",
            "сентября": "09",
            "сен": "09",
            "октября": "10",
            "окт": "10",
            "ноября": "11",
            "ноя": "11",
            "декабря": "12",
            "дек": "12",
        }.get(month_name.strip().casefold().rstrip("."))

    async def _field_value(self, field: Locator) -> str:
        value = (await field.input_value()).strip()
        if value:
            return value
        return ((await field.get_attribute("placeholder")) or "").strip()

    def _intervals_match(
        self,
        expected: ProductInterval,
        actual: ProductInterval,
    ) -> bool:
        return (
            expected.start_date == actual.start_date
            and expected.end_date == actual.end_date
            and actual.start_time.startswith(expected.start_time)
            and actual.end_time.startswith(expected.end_time)
        )

    async def _close_external_page(self, page: Page) -> None:
        try:
            if not page.is_closed():
                await page.close()
        except Exception:
            pass
        try:
            if not self.page.is_closed():
                await self.page.bring_to_front()
                await maximize_page_window(self.page)
        except Exception:
            pass

    def _matrix_content_response(self, response: Response) -> bool:
        return same_origin_content_response(
            response,
            base_url=settings.BASE_URL,
        )


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split()).casefold()
