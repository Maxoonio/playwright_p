from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import settings
from ctf_metrics import (
    AggregateMetricsExpectation,
    QuestionMetricsExpectation,
    answered_question_metrics,
)
from pages.basic_modules_page import ProductInterval, normalize_windows_path_escaping
from playwright.async_api import Locator, Page, Response, expect
from utils.logging import log_step
from utils.network import (
    perform_and_wait_for_response,
    response_path_is,
)
from utils.ui import safe_click


@dataclass(frozen=True)
class DomMetricValue:
    value: int


@dataclass(frozen=True)
class ScenarioMetricsSnapshot:
    accuracy: DomMetricValue
    speed: DomMetricValue
    points: DomMetricValue


@dataclass(frozen=True)
class TaskMetricsSnapshot:
    base_points: int
    adjustments: tuple[int, ...]
    displayed_points: int
    elapsed_seconds: int
    accuracy: DomMetricValue
    speed: DomMetricValue


@dataclass(frozen=True)
class FinalPerformanceSnapshot:
    accuracy_percent: int
    speed_percent: int


class CtfScenarioPage:
    """DOM/network-драйвер идеального прохождения CTF."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.current_stage: int | None = None
        self.current_question: int | None = None
        self._question_start_marks: dict[str, float] = {}

    def visible_task_cards(self) -> Locator:
        return self.page.locator(
            "[id^='matrix_scenarios_task_']:visible, .scenario-tasks-card:visible"
        )

    def indexed_task_cards(self) -> Locator:
        """Returns one stable outer DOM node per visible CTF question."""

        return self.page.locator("[id^='matrix_scenarios_task_']:visible")

    def task_card(self, question_index: int) -> Locator:
        return self.page.locator(
            f"#matrix_scenarios_task_{question_index}:visible"
        ).first

    def tasks_button(self) -> Locator:
        return self.page.get_by_role(
            "button",
            name=re.compile(r"^\s*К заданиям\s*$", re.IGNORECASE),
        ).first

    def tasks_tab(self) -> Locator:
        return (
            self.page.locator("[kbqtablabelwrapper]:visible, .kbq-tab-label:visible")
            .filter(
                has_text=re.compile(
                    r"Задания\s+и\s+результаты",
                    re.IGNORECASE,
                )
            )
            .first
        )

    def description_tab(self) -> Locator:
        return (
            self.page.locator("[kbqtablabelwrapper]:visible, .kbq-tab-label:visible")
            .filter(
                has_text=re.compile(
                    r"^\s*Описание\s+задачи\s*$",
                    re.IGNORECASE,
                )
            )
            .first
        )

    def product_link(self) -> Locator:
        """Returns the visible external product link from the active CTF tab."""

        return (
            self.page.locator(
                "a[target='_blank'].statistics-link-cursor:visible, "
                "a[target='_blank'][kbq-link]:visible"
            )
            .filter(
                has_text=re.compile(
                    rf"^\s*{re.escape(settings.PRODUCT_NAME)}\s*$",
                    re.IGNORECASE,
                )
            )
            .first
        )

    def sandbox_ctf_tasks_content(self) -> Locator:
        """Returns the innermost active CTF tab containing its own interval."""

        return (
            self.page.locator(
                "kbq-tab-body.kbq-tab-body__active:visible, "
                ".kbq-tab-body.kbq-tab-body__active:visible"
            )
            .filter(
                has_text=re.compile(
                    r"Временной\s+интервал\s+для\s+поиска\s+решения",
                    re.IGNORECASE,
                )
            )
            .last
        )

    def sandbox_ctf_product_link(self) -> Locator:
        """Returns PT Sandbox link from the active CTF task tab only."""

        return (
            self.sandbox_ctf_tasks_content()
            .locator(
                "a[target='_blank'].statistics-link-cursor:visible, "
                "a[target='_blank'][kbq-link]:visible"
            )
            .filter(
                has_text=re.compile(
                    rf"^\s*{re.escape(settings.PRODUCT_NAME)}\s*$",
                    re.IGNORECASE,
                )
            )
            .first
        )

    async def open_description(self) -> None:
        """Открывает описание CTF до запуска таймера первой карточки."""

        tab = self.description_tab()
        await expect(tab).to_be_visible(timeout=settings.CTF_TRANSITION_TIMEOUT_MS)
        await safe_click(tab)
        await expect(tab).to_have_class(
            re.compile(r"\bkbq-selected\b"), timeout=settings.CTF_TRANSITION_TIMEOUT_MS
        )
        log_step("[CTF][DESCRIPTION] Описание открыто до запуска первого задания")

    async def activate_tasks_tab_without_expanding(self) -> None:
        """Activates the tasks tab but does not start or expand a task card."""

        tab = self.tasks_tab()
        await expect(tab).to_be_visible(timeout=settings.CTF_TRANSITION_TIMEOUT_MS)
        if await self._tasks_tab_is_selected(
            tab
        ) and await self._wait_for_task_cards_visible(timeout_ms=750):
            log_step(
                "[CTF][TASKS] Вкладка «Задания и результаты» уже активна; "
                "повторный клик не требуется"
            )
            return

        await safe_click(tab)
        await expect(self.visible_task_cards().first).to_be_visible(
            timeout=settings.CTF_TRANSITION_TIMEOUT_MS
        )
        log_step(
            "[CTF][TASKS] Вкладка «Задания и результаты» нажата; "
            "карточки пока не раскрываем"
        )

    async def open_tasks(self) -> None:
        cards_were_visible = await self._wait_for_task_cards_visible(timeout_ms=750)
        if cards_were_visible:
            already_open_message = "уже открыта, задания видимы"
            log_step(
                f"[CTF][TASKS] Вкладка «Задания и результаты» {already_open_message}"
            )
        else:
            tab = self.tasks_tab()
            if await tab.is_visible():
                if await self._tasks_tab_is_selected(tab):
                    await expect(self.visible_task_cards().first).to_be_visible(
                        timeout=settings.CTF_TRANSITION_TIMEOUT_MS
                    )
                    log_step(
                        "[CTF][TASKS] Вкладка «Задания и результаты» "
                        "уже выбрана, задания загружены"
                    )
                else:
                    await safe_click(tab)
                    await expect(self.visible_task_cards().first).to_be_visible(
                        timeout=settings.CTF_TRANSITION_TIMEOUT_MS
                    )
                    log_step("[CTF][TASKS] Вкладка «Задания и результаты» открыта")
            else:
                button = self.tasks_button()
                await expect(button).to_be_visible(
                    timeout=settings.CTF_TRANSITION_TIMEOUT_MS
                )
                await safe_click(button)
                await expect(self.visible_task_cards().first).to_be_visible(
                    timeout=settings.CTF_TRANSITION_TIMEOUT_MS
                )
                log_step("[CTF][TASKS] Кнопка «К заданиям» открыла задания")

        await self._ensure_first_unresolved_task_expanded()

    async def _ensure_first_unresolved_task_expanded(self) -> None:
        if await self._expanded_task_cards().count() > 0:
            log_step("[CTF][TASKS] Карточка задания уже раскрыта")
            return

        cards = self.page.locator("[id^='matrix_scenarios_task_']:visible")
        cards_count = await cards.count()
        for index in range(cards_count):
            task = cards.nth(index)
            resolved = await task.evaluate(
                r"""
                (element) => {
                    const root = element.matches(".scenario-tasks-card")
                        ? element
                        : element.querySelector(".scenario-tasks-card") || element;
                    return root.classList.contains("completed")
                        || root.getAttribute("data-state") === "success"
                        || root.getAttribute("data-status") === "success"
                        || Boolean(element.querySelector(
                            ".scenario-tasks-card.completed, "
                            + "[data-state='success'], "
                            + "[data-status='success'], "
                            + "img[src*='forbidden.svg']"
                        ));
                }
                """
            )
            if resolved:
                continue

            await self._ensure_question_expanded(task)
            log_step(
                "[CTF][TASKS] Первая нерешённая карточка задания "
                f"раскрыта по DOM (индекс={index})"
            )
            return

        log_step("[CTF][TASKS] Нерешённых карточек для первоначального раскрытия нет")

    async def _wait_for_task_cards_visible(
        self,
        *,
        timeout_ms: int,
    ) -> bool:
        try:
            await expect(self.visible_task_cards().first).to_be_visible(
                timeout=timeout_ms
            )
            return True
        except Exception:
            return False

    async def _tasks_tab_is_selected(self, tab: Locator) -> bool:
        try:
            return await tab.evaluate(
                """
                (element) => {
                    const text = element.innerText || "";
                    if (!/Задания\\s+и\\s+результаты/i.test(text)) {
                        return false;
                    }
                    return element.classList.contains("kbq-selected")
                        || element.getAttribute("aria-selected") === "true"
                        || element.getAttribute("tabindex") === "0";
                }
                """
            )
        except Exception:
            return False

    async def open_completed_tasks_and_results(self) -> None:
        metrics = self.page.locator(".scenarios-tasks-metric-container:visible").first
        if await metrics.is_visible():
            return

        tab = self.tasks_tab()
        if await tab.is_visible():
            await safe_click(tab)
        else:
            await self.open_tasks()

        await expect(metrics).to_be_visible(timeout=settings.CTF_METRICS_TIMEOUT_MS)
        log_step(
            "[CTF][METRICS] Вкладка «Задания и результаты» "
            "открыта, общая статистика видима"
        )

    def answer_field(self, task: Locator) -> Locator:
        return task.locator(
            "input[kbqinput]:visible, "
            "input:visible:not([type='hidden']):not([type='checkbox'])"
            ":not([type='radio']):not([type='button']):not([type='submit']), "
            "textarea:visible"
        ).first

    def elapsed_timer(self, task: Locator) -> Locator:
        return task.locator(
            ".height-30 span.scenario-tasks-card__timer:not(.mx-color-grey)"
        ).first

    def duration_timer(self, task: Locator) -> Locator:
        return task.locator(
            ".scenario-tasks-card-title span.scenario-tasks-card__timer.mx-color-grey"
        ).first

    async def read_product_interval(self) -> ProductInterval:
        text_interval = await self._read_visible_product_interval_text()
        if text_interval is not None:
            log_step(
                "[CTF][PRODUCT][TEXT] "
                f"Интервал из текущего CTF-задания: {text_interval}"
            )
            return text_interval

        href_interval = await self._read_product_interval_from_link()
        if href_interval is not None:
            log_step(
                "[CTF][PRODUCT][LINK] "
                f"Интервал перехода в {settings.PRODUCT_NAME}: "
                f"{href_interval}"
            )
            return href_interval

        raise AssertionError(
            f"Не удалось считать CTF-интервал перед переходом в {settings.PRODUCT_NAME}"
        )

    async def read_sandbox_ctf_product_interval(self) -> ProductInterval:
        """Reads the exact visible Sandbox interval text rendered in the CTF tab."""

        ctf_content = self.sandbox_ctf_tasks_content()
        await expect(ctf_content).to_be_visible(
            timeout=settings.CTF_TRANSITION_TIMEOUT_MS
        )
        link = self.sandbox_ctf_product_link()
        await expect(link).to_be_visible(timeout=settings.CTF_TRANSITION_TIMEOUT_MS)
        interval_text_nodes = ctf_content.locator(
            "div.ng-star-inserted:visible > div:visible"
        ).filter(
            has_text=re.compile(
                r"^\s*Временной\s+интервал\s+для\s+поиска\s+решения\s+"
                r"\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}(?::\d{2})?\s*-\s*"
                r"\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}(?::\d{2})?\s*$",
                re.IGNORECASE,
            )
        )

        interval_texts = await interval_text_nodes.all_inner_texts()
        parsed_dom_intervals = [
            (text, interval)
            for text in interval_texts
            if (interval := self._parse_product_interval_text(text)) is not None
        ]
        if not parsed_dom_intervals:
            raise AssertionError(
                "В активной вкладке CTF не найден точный видимый DOM-блок "
                "«Временной интервал для поиска решения <от> - <до>»"
            )

        href = (await link.get_attribute("href")) or ""
        href_interval = self._parse_sandbox_product_interval_href(href)
        unique_dom_intervals = list(
            dict.fromkeys(interval for _, interval in parsed_dom_intervals)
        )
        if href_interval is not None:
            matching_intervals = [
                interval
                for interval in unique_dom_intervals
                if interval == href_interval
            ]
            if not matching_intervals:
                rendered = "; ".join(
                    f"{text.strip()} -> {interval}"
                    for text, interval in parsed_dom_intervals
                )
                raise AssertionError(
                    "Интервал точного CTF DOM-блока не совпал с "
                    "filters.time.range CTF-ссылки PT Sandbox: "
                    f"DOM=[{rendered}]; link={href_interval}"
                )
            dom_interval = matching_intervals[0]
        elif len(unique_dom_intervals) == 1:
            dom_interval = unique_dom_intervals[0]
        else:
            raise AssertionError(
                "В активной вкладке CTF найдено несколько различных интервалов, "
                "а CTF-ссылка PT Sandbox не содержит filters.time.range: "
                + "; ".join(str(interval) for interval in unique_dom_intervals)
            )

        selected_text = next(
            text.strip()
            for text, interval in parsed_dom_intervals
            if interval == dom_interval
        )
        log_step(
            "[CTF][PRODUCT][SANDBOX] "
            f"Точный DOM-текст: {selected_text!r}; интервал={dom_interval}"
        )
        return dom_interval

    async def verify_stage_opened(
        self,
        stage_number: int,
        expected_questions: int,
    ) -> None:
        await self.verify_stage_opened_without_question_interaction(
            stage_number,
            expected_questions,
        )
        await self._ensure_actionbar_open()
        await self.visible_task_cards().first.click(
            trial=True,
            timeout=settings.CTF_TRANSITION_TIMEOUT_MS,
        )

    async def verify_stage_opened_without_question_interaction(
        self,
        stage_number: int,
        expected_questions: int,
    ) -> None:
        """Checks the active stage without clicking or scrolling a task card."""

        self.current_stage = stage_number
        self.current_question = None

        continue_button = self._stage_continue_button()
        if await continue_button.is_visible():
            log_step(
                f"[CTF][STAGE {stage_number}] "
                "Осталось окно предыдущего этапа, закрываем по DOM"
            )
            await self.click_stage_continue()

        await self._handle_timer_warning()
        await expect(
            self.page.get_by_text(f"Этап {stage_number}", exact=False).first
        ).to_be_visible(timeout=settings.CTF_TRANSITION_TIMEOUT_MS)
        for question_index in range(expected_questions):
            await expect(self.task_card(question_index)).to_be_visible(
                timeout=settings.CTF_TRANSITION_TIMEOUT_MS
            )

        visible_ids = await self.indexed_task_cards().evaluate_all(
            "cards => cards.map(card => card.id)"
        )
        log_step(
            f"[CTF][STAGE {stage_number}] Открыт, "
            f"ожидаемых вопросов={expected_questions}, DOM-id={visible_ids}"
        )

    async def open_question(
        self,
        question_index: int,
    ) -> Locator:
        self.current_question = question_index + 1
        await self._handle_timer_warning()
        task = self.task_card(question_index)
        await expect(task).to_be_visible(timeout=settings.CTF_TRANSITION_TIMEOUT_MS)

        if await self._question_is_expanded_and_ready(task):
            self._question_start_marks[self._question_key()] = await self.page.evaluate(
                "performance.now()"
            )
            log_step(
                f"{self._position_label()}: уже раскрытая карточка переиспользована"
            )
            return task

        await self._collapse_open_question()
        task = self.task_card(question_index)
        await expect(task).to_be_visible(timeout=settings.CTF_TRANSITION_TIMEOUT_MS)

        start_response: Response | None = None

        def remember_start(response: Response) -> None:
            nonlocal start_response
            if response_path_is(
                response,
                path="/api/ctf/exercise/start/",
                method="POST",
            ):
                start_response = response

        self.page.on("response", remember_start)
        try:
            await self._ensure_question_expanded(task)
        finally:
            self.page.remove_listener("response", remember_start)

        if start_response is not None:
            log_step(
                "[NETWORK] Старт CTF-задания "
                f"{self._position_label()}: POST "
                "/api/ctf/exercise/start/ -> "
                f"{start_response.status}"
            )
            if not start_response.ok:
                raise AssertionError(f"CTF start вернул HTTP {start_response.status}")

        self._question_start_marks[self._question_key()] = await self.page.evaluate(
            "performance.now()"
        )
        return task

    async def fill_answer(
        self,
        task: Locator,
        answer: str,
    ) -> None:
        normalized_answer = normalize_windows_path_escaping(answer)
        if normalized_answer != answer:
            log_step(
                "[CTF][ANSWER] Нормализовано экранирование Windows-пути "
                f"из GitLab: символов={len(answer)}->{len(normalized_answer)}, "
                f"разделителей={answer.count('\\')}->"
                f"{normalized_answer.count('\\')}"
            )
        field = self.answer_field(task)
        await expect(field).to_be_visible(timeout=settings.CTF_TRANSITION_TIMEOUT_MS)
        await expect(field).to_be_enabled()
        await self._replace_answer_field_value(field, normalized_answer)
        await expect(field).to_have_value(normalized_answer)

    async def _replace_answer_field_value(
        self,
        field: Locator,
        answer: str,
    ) -> None:
        """Атомарно заменяет старый CTF-ответ без пустого Angular-состояния."""

        if not answer:
            raise AssertionError("CTF-ответ не может быть пустым")

        await field.scroll_into_view_if_needed()
        await field.click(timeout=settings.CTF_TRANSITION_TIMEOUT_MS)
        await field.focus()
        await field.fill(answer)
        if await field.input_value() == answer:
            return

        await field.select_text()
        await self.page.keyboard.insert_text(answer)
        if await field.input_value() == answer:
            return

        await field.evaluate(
            """
            (element, value) => {
                const prototype = element instanceof HTMLTextAreaElement
                    ? HTMLTextAreaElement.prototype
                    : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(
                    prototype,
                    "value"
                )?.set;
                if (setter) setter.call(element, value);
                else element.value = value;
                const inputEvent = typeof InputEvent === "function"
                    ? new InputEvent("input", {
                        bubbles: true,
                        composed: true,
                        inputType: "insertReplacementText",
                        data: value
                    })
                    : new Event("input", { bubbles: true, composed: true });
                element.dispatchEvent(inputEvent);
                element.dispatchEvent(new Event("change", {
                    bubbles: true,
                    composed: true
                }));
            }
            """,
            answer,
        )
        await expect(field).to_have_value(
            answer,
            timeout=settings.CTF_TRANSITION_TIMEOUT_MS,
        )

    async def submit_answer(self, task: Locator) -> int:
        await self._wait_for_timer_tick(task)
        send = await self._resolve_send_button(task)
        _, response = await perform_and_wait_for_response(
            self.page,
            lambda: safe_click(send),
            predicate=lambda candidate: response_path_is(
                candidate,
                path="/api/ctf/exercise/submit/",
                method="POST",
            ),
            description=(f"Отправка CTF-ответа {self._position_label()}"),
            require_ok=False,
        )
        if not response.ok:
            raise AssertionError(f"CTF submit вернул HTTP {response.status}")
        return response.status

    async def verify_answer_accepted(
        self,
        task: Locator,
    ) -> None:
        await expect(
            task.locator(".scenario-tasks-card.completed:visible").first
        ).to_be_visible(timeout=settings.CTF_ANSWER_STATE_TIMEOUT_MS)

    async def verify_current_stage_yellow_circle(
        self,
        stage_number: int,
    ) -> None:
        await self._ensure_actionbar_open()
        stage = self.page.locator(
            "matrix-actionbar:visible .actionbar__step-container"
        ).nth(stage_number - 1)
        circle = stage.locator(
            "div.actionbar__step__circle.actionbar__step__circle-yellow:visible"
        ).first
        await expect(circle).to_be_visible(timeout=settings.CTF_ANSWER_STATE_TIMEOUT_MS)
        log_step(
            f"[CTF][STAGE {stage_number}][STATUS] Текущий этап отмечен желтым кружком"
        )

    async def verify_stage_success_status(
        self,
        stage_number: int,
    ) -> None:
        await self._ensure_actionbar_open()
        completed_stage = self.page.locator(
            "matrix-actionbar:visible img.actionbar__step[src]:visible"
        ).nth(stage_number - 1)
        await expect(completed_stage).to_have_attribute(
            "src",
            re.compile(
                re.escape("success-filled.svg"),
                re.IGNORECASE,
            ),
            timeout=settings.CTF_ANSWER_STATE_TIMEOUT_MS,
        )
        log_step(
            f"[CTF][STAGE {stage_number}][STATUS] "
            "Этап отмечен зеленой иконкой выполнения"
        )

    async def wait_for_stage_completion_dialog(self) -> Locator:
        dialog = (
            self.page.locator(".cdk-overlay-pane:visible, .kbq-modal-content:visible")
            .filter(
                has_text=re.compile(
                    r"Отличная работа|"
                    r"Вы успешно выполнили все задания Этапа|"
                    r"Этапы атаки",
                    re.IGNORECASE,
                )
            )
            .last
        )
        await expect(dialog).to_be_visible(timeout=settings.CTF_TRANSITION_TIMEOUT_MS)
        await expect(self._stage_continue_button()).to_be_visible(
            timeout=settings.CTF_TRANSITION_TIMEOUT_MS
        )
        return dialog

    async def click_stage_continue(self) -> None:
        button = self._stage_continue_button()
        for _ in range(3):
            await self._click_with_fallback(button)
            try:
                await button.wait_for(
                    state="hidden",
                    timeout=3_000,
                )
                return
            except Exception:
                button = self._stage_continue_button()
        raise AssertionError("Окно завершения CTF-этапа не закрылось")

    async def verify_final_dialog(self) -> Locator:
        dialog = self.page.locator("matrix-congratulation:visible").last
        await expect(dialog).to_be_visible(timeout=settings.CTF_TRANSITION_TIMEOUT_MS)
        await expect(
            dialog.get_by_text(re.compile(r"поздравляем", re.IGNORECASE)).first
        ).to_be_visible(timeout=settings.CTF_TRANSITION_TIMEOUT_MS)
        await expect(
            dialog.locator("textarea[formcontrolname='description']").first
        ).to_be_visible(timeout=settings.CTF_TRANSITION_TIMEOUT_MS)
        return dialog

    async def final_dialog_is_visible(
        self,
        *,
        timeout: int = 2_000,
    ) -> bool:
        dialog = self.page.locator("matrix-congratulation:visible").last
        try:
            await expect(dialog).to_be_visible(timeout=timeout)
        except Exception:
            return False
        return True

    async def actionbar_scheme_is_available(self) -> bool:
        """Return whether the current CTF stage exposes the optional sidebar."""

        sidebar = self.page.locator(
            ".scenarios-result__right-container:visible, "
            ".basic-scenario-actionbar-show:visible"
        ).first
        try:
            await expect(sidebar).to_be_visible(timeout=1_500)
        except Exception:
            return False
        return True

    async def verify_actionbar_materials(self) -> None:
        """Verify the inline and modal attack schemes in the stage sidebar."""

        await self.verify_actionbar_inline_scheme()
        await self.verify_actionbar_scheme_dialog()

    async def verify_actionbar_inline_scheme(self) -> None:
        await self._ensure_actionbar_open_from_inline_scheme()
        image = self._actionbar_inline_scheme_image()
        await expect(image).to_be_visible(timeout=settings.CTF_MATERIAL_TIMEOUT_MS)
        await expect(image).to_have_attribute(
            "src",
            re.compile(r"\S+"),
            timeout=settings.CTF_MATERIAL_TIMEOUT_MS,
        )
        await self._wait_for_image_loaded(image)
        image_src = (await image.get_attribute("src")) or "-"
        log_step(
            f"[CTF][STAGE {self.current_stage}][ACTIONBAR] "
            f"Встроенная схема загружена: {image_src}"
        )

    async def verify_actionbar_scheme_dialog(self) -> None:
        await self._verify_actionbar_scheme_dialog()

    async def _verify_actionbar_scheme_dialog(self) -> None:
        await self._ensure_actionbar_open_from_inline_scheme()
        scheme_link = (
            self._actionbar_right_container()
            .locator("span.statistics-link.body-3.cursor:visible")
            .filter(
                has_text=re.compile(
                    r"^\s*Схема атаки\s*$",
                    re.IGNORECASE,
                )
            )
            .first
        )
        close = self.page.locator("button#reportModalSubmit:visible").first
        modal_image_src = "-"
        try:
            await expect(scheme_link).to_be_visible(
                timeout=settings.CTF_MATERIAL_TIMEOUT_MS
            )
            await self._click_with_fallback(scheme_link)
            await expect(close).to_be_visible(timeout=settings.CTF_MATERIAL_TIMEOUT_MS)
            modal_image = self.page.locator(
                ".scheme-dialog-container img[src]:visible"
            ).last
            await expect(modal_image).to_be_visible(
                timeout=settings.CTF_MATERIAL_TIMEOUT_MS
            )
            await self._wait_for_image_loaded(modal_image)
            modal_image_src = (await modal_image.get_attribute("src")) or "-"
        finally:
            await self._close_modal_by_button(close)

        log_step(
            f"[CTF][STAGE {self.current_stage}][ACTIONBAR] "
            f"Модальное окно схемы проверено: {modal_image_src}"
        )

    async def verify_stage_completion_materials(
        self,
        *,
        final_stage: bool,
    ) -> None:
        if not final_stage:
            await self.verify_stage_inline_scheme(final_stage=final_stage)
        await self.verify_stage_scheme_dialog(final_stage=final_stage)
        await self.verify_stage_video(final_stage=final_stage)

    async def verify_stage_inline_scheme(
        self,
        *,
        final_stage: bool,
    ) -> None:
        await self._wait_for_completion_parent(final_stage=final_stage)
        inline_image = self.page.locator(
            "img.congratulation-stage__image"
            "[src*='/media/scheme/']:visible, "
            ".congratulation-stage__second-description "
            "img[src*='/media/scheme/']:visible"
        ).last
        await expect(inline_image).to_be_visible(
            timeout=settings.CTF_MATERIAL_TIMEOUT_MS
        )
        await self._wait_for_image_loaded(inline_image)

    async def verify_stage_scheme_dialog(
        self,
        *,
        final_stage: bool,
    ) -> None:
        await self._wait_for_completion_parent(final_stage=final_stage)
        scheme_link = self._stage_completion_material_link(
            text=re.compile(
                r"^Схема этап(а|ов) атаки$",
                re.IGNORECASE,
            ),
            final_stage=final_stage,
        )
        close = self.page.locator("button#reportModalSubmit:visible").first
        try:
            await expect(scheme_link).to_be_visible(
                timeout=settings.CTF_MATERIAL_TIMEOUT_MS
            )
            await self._click_with_fallback(scheme_link)
            await expect(close).to_be_visible(timeout=settings.CTF_MATERIAL_TIMEOUT_MS)
            modal_image = self.page.locator(
                "img.scheme-dialog-container__image"
                "[src*='/media/scheme/']:visible, "
                ".scheme-dialog-container__second-description "
                "img[src*='/media/scheme/']:visible"
            ).last
            await expect(modal_image).to_be_visible(
                timeout=settings.CTF_MATERIAL_TIMEOUT_MS
            )
            await self._wait_for_image_loaded(modal_image)
        finally:
            await self._close_modal_by_button(close)

        await self._wait_for_completion_parent(final_stage=final_stage)

    async def verify_stage_video(
        self,
        *,
        final_stage: bool,
    ) -> None:
        await self._wait_for_completion_parent(final_stage=final_stage)
        close = self.page.locator("button#videoModalCloseBtn:visible").last
        try:
            await self._click_stage_completion_video_link(
                final_stage=final_stage,
                close=close,
            )
            await expect(close).to_be_visible(timeout=settings.CTF_MATERIAL_TIMEOUT_MS)
            await self._wait_for_stage_video_present()
        finally:
            await self._close_modal_by_button(close)

        await self._wait_for_completion_parent(final_stage=final_stage)

    async def handle_final_feedback(
        self,
        description: str,
    ) -> None:
        dialog = await self.verify_final_dialog()
        textarea = dialog.locator(
            "textarea[formcontrolname='description'], textarea.kbq-textarea, textarea"
        ).first
        submit = (
            dialog.locator("button#congratulationModalSubmit:visible")
            .filter(
                has_text=re.compile(
                    r"^\s*Отправить\s*$",
                    re.IGNORECASE,
                )
            )
            .last
        )

        last_error: Exception | None = None
        for attempt in range(
            1,
            settings.FINAL_FEEDBACK_MAX_ATTEMPTS + 1,
        ):
            observed: list[Response] = []

            def remember_feedback(response: Response) -> None:
                if not observed and response_path_is(
                    response,
                    path="/api/common/scenario/submit_feedback/",
                    method="POST",
                ):
                    observed.append(response)

            self.page.on("response", remember_feedback)
            try:
                await textarea.fill("")
                await textarea.fill(description)
                await expect(textarea).to_have_value(description)
                await self._click_final_feedback_submit(submit)
                if await self._wait_for_final_dialog_hidden(timeout_ms=5_000):
                    status = observed[0].status if observed else "DOM"
                    log_step(
                        f"[CTF][FINAL][FEEDBACK] Окно закрыто после отправки: {status}"
                    )
                    return

                if observed and not observed[0].ok:
                    raise AssertionError(
                        f"Feedback API вернул HTTP {observed[0].status}"
                    )
                raise AssertionError("Поздравительное окно осталось открытым")
            except Exception as exc:
                last_error = exc
                log_step(
                    f"[CTF][FINAL][FEEDBACK] Попытка {attempt} не подтверждена: {exc}"
                )
                if observed and not observed[0].ok:
                    break
            finally:
                self.page.remove_listener(
                    "response",
                    remember_feedback,
                )

        if await self._close_final_dialog(dialog):
            log_step(
                "[CTF][FINAL][FEEDBACK] "
                "Отправка не подтверждена, окно закрыто крестиком"
            )
            return

        raise AssertionError(
            "Финальное окно CTF не удалось отправить или закрыть"
        ) from last_error

    async def read_task_duration_seconds(
        self,
        task: Locator,
    ) -> int:
        return await self._read_timer_seconds(
            self.duration_timer(task),
            timer_name="duration",
        )

    async def read_task_elapsed_seconds(
        self,
        task: Locator,
        *,
        timeout_ms: int = settings.CTF_METRICS_TIMEOUT_MS,
    ) -> int:
        return await self._read_timer_seconds(
            self.elapsed_timer(task),
            timer_name="timer",
            timeout_ms=timeout_ms,
        )

    async def read_submission_elapsed_seconds(
        self,
        task: Locator,
    ) -> int:
        await self._wait_for_timer_tick(task)
        task_id = await task.get_attribute("id")
        if task_id:
            await self.page.wait_for_function(
                """
                (taskId) => {
                    const timer = document.querySelector(
                        `#${taskId} .height-30 `
                        + "span.scenario-tasks-card__timer"
                        + ":not(.mx-color-grey)"
                    );
                    if (!timer) return false;
                    const parts = (timer.textContent || "")
                        .trim()
                        .split(":")
                        .map(Number);
                    if (
                        (parts.length !== 2 && parts.length !== 3)
                        || parts.some(Number.isNaN)
                    ) return false;
                    const seconds = parts.length === 2
                        ? parts[0] * 60 + parts[1]
                        : parts[0] * 3600
                            + parts[1] * 60
                            + parts[2];
                    return seconds > 0;
                }
                """,
                arg=task_id,
                timeout=3_000,
            )
        return await self.read_task_elapsed_seconds(task)

    async def verify_completed_question_metrics(
        self,
        *,
        question_index: int,
        duration_seconds: int,
        base_points: int,
    ) -> QuestionMetricsExpectation:
        task = self.task_card(question_index)
        await self._ensure_completed_task_metrics_visible(task)
        snapshot = await self._wait_for_task_metrics(
            task,
            accuracy_percent=100,
            duration_seconds=duration_seconds,
            base_points=base_points,
        )
        expected = answered_question_metrics(
            attempt_count=1,
            duration_seconds=duration_seconds,
            actual_seconds=snapshot.elapsed_seconds,
            base_points=base_points,
        )

        if snapshot.base_points != expected.base_points:
            raise AssertionError(
                "Базовые баллы карточки не совпали: "
                f"DOM={snapshot.base_points}, "
                f"GitLab={expected.base_points}"
            )
        if snapshot.accuracy.value != expected.accuracy_percent:
            raise AssertionError(
                "Точность карточки не совпала: "
                f"DOM={snapshot.accuracy.value}%, "
                f"ожидалось={expected.accuracy_percent}%"
            )
        if abs(snapshot.speed.value - expected.speed_percent) > 1:
            raise AssertionError(
                "Скорость карточки не совпала: "
                f"DOM={snapshot.speed.value}, "
                f"ожидалось={expected.speed_percent}"
            )
        if snapshot.displayed_points != expected.awarded_points:
            raise AssertionError(
                "Баллы карточки не совпали: "
                f"DOM={snapshot.displayed_points}, "
                f"ожидалось={expected.awarded_points}"
            )

        log_step(
            f"[CTF][STAGE {self.current_stage}]"
            f"[Q{self.current_question}][METRICS] "
            f"баллы={snapshot.displayed_points}, "
            f"точность={snapshot.accuracy.value}%, "
            f"скорость={snapshot.speed.value}, "
            f"время={snapshot.elapsed_seconds}с"
        )
        return expected

    async def verify_scenario_metrics(
        self,
        expected: AggregateMetricsExpectation,
    ) -> ScenarioMetricsSnapshot:
        snapshot = await self._wait_for_scenario_metrics(expected=expected)
        if snapshot.accuracy.value != expected.accuracy_percent:
            raise AssertionError(
                "Общая точность не совпала: "
                f"DOM={snapshot.accuracy.value}%, "
                f"ожидалось={expected.accuracy_percent}%"
            )
        if abs(snapshot.speed.value - expected.speed_percent) > 1:
            raise AssertionError(
                "Общая скорость не совпала: "
                f"DOM={snapshot.speed.value}, "
                f"ожидалось={expected.speed_percent}"
            )
        if snapshot.points.value != expected.points:
            raise AssertionError(
                "Общие баллы не совпали: "
                f"DOM={snapshot.points.value}, "
                f"ожидалось={expected.points}"
            )

        log_step(
            "[CTF][METRICS][TOTAL] "
            f"баллы={snapshot.points.value}, "
            f"точность={snapshot.accuracy.value}%, "
            f"скорость={snapshot.speed.value}"
        )
        return snapshot

    async def verify_final_performance_metrics(
        self,
        expected: AggregateMetricsExpectation,
    ) -> FinalPerformanceSnapshot:
        dialog = await self.verify_final_dialog()
        label = (
            dialog.locator("label:visible")
            .filter(
                has_text=re.compile(
                    r"скорост.*точност",
                    re.IGNORECASE,
                )
            )
            .last
        )
        await expect(label).to_be_visible(timeout=settings.CTF_METRICS_TIMEOUT_MS)
        text = " ".join((await label.inner_text()).split())
        speed_match = re.search(
            r"скорост\w*\s+(\d+)",
            text,
            re.IGNORECASE,
        )
        accuracy_match = re.search(
            r"точност\w*\s+(\d+)",
            text,
            re.IGNORECASE,
        )
        if speed_match is None or accuracy_match is None:
            raise AssertionError(
                f"Не удалось прочитать скорость и точность из финального окна: {text!r}"
            )

        snapshot = FinalPerformanceSnapshot(
            speed_percent=int(speed_match.group(1)),
            accuracy_percent=int(accuracy_match.group(1)),
        )
        if snapshot.accuracy_percent != expected.accuracy_percent:
            raise AssertionError(
                "Финальная точность не совпала: "
                f"DOM={snapshot.accuracy_percent}, "
                f"ожидалось={expected.accuracy_percent}"
            )
        if abs(snapshot.speed_percent - expected.speed_percent) > 1:
            raise AssertionError(
                "Финальная скорость не совпала: "
                f"DOM={snapshot.speed_percent}, "
                f"ожидалось={expected.speed_percent}"
            )

        log_step(
            "[CTF][FINAL][METRICS] "
            f"точность={snapshot.accuracy_percent}, "
            f"скорость={snapshot.speed_percent}, "
            f"общие баллы={expected.points}"
        )
        return snapshot

    async def _ensure_completed_task_metrics_visible(
        self,
        task: Locator,
    ) -> None:
        accuracy = self._metric_span_by_icon(
            task,
            "accuracy-grey.svg",
        )
        speed = self._metric_span_by_icon(
            task,
            "speed-one-grey.svg",
        )
        if await accuracy.is_visible() and await speed.is_visible():
            return

        chevron = task.locator("[kbq-icon='kbq-chevron-down-s_24']:visible").first
        candidates = (
            chevron.locator("xpath=..").first,
            chevron,
            task.locator(".scenario-tasks-card-title-question:visible").first,
            task.locator(".scenario-tasks-card-title:visible").first,
        )
        for candidate in candidates:
            try:
                if not await candidate.is_visible():
                    continue
                await self._click_with_fallback(candidate)
                await expect(accuracy).to_be_visible(
                    timeout=settings.CTF_METRICS_TIMEOUT_MS
                )
                await expect(speed).to_be_visible(
                    timeout=settings.CTF_METRICS_TIMEOUT_MS
                )
                return
            except Exception:
                continue

        raise AssertionError(
            "Не удалось раскрыть завершенную CTF-карточку для чтения метрик"
        )

    async def _wait_for_task_metrics(
        self,
        task: Locator,
        *,
        accuracy_percent: int,
        duration_seconds: int,
        base_points: int,
    ) -> TaskMetricsSnapshot:
        await expect(
            self._metric_span_by_icon(
                task,
                "accuracy-grey.svg",
            )
        ).to_have_text(
            re.compile(rf"^\s*{accuracy_percent}\s*%\s*$"),
            timeout=settings.CTF_METRICS_TIMEOUT_MS,
        )

        task_id = await task.get_attribute("id")
        if not task_id:
            raise AssertionError("У CTF-карточки нет DOM id")
        await self.page.wait_for_function(
            r"""
            ({ taskId, durationSeconds, basePoints }) => {
                const task = document.getElementById(taskId);
                const points = task?.querySelector(
                    ".scenario-tasks-card-points"
                );
                const timer = task?.querySelector(
                    ".height-30 "
                    + "span.scenario-tasks-card__timer"
                    + ":not(.mx-color-grey)"
                );
                const speedIcon = task?.querySelector(
                    "img[src$='speed-one-grey.svg']"
                );
                const speedSpan = speedIcon
                    ?.parentElement
                    ?.querySelector("span");
                if (!points || !timer || !speedSpan) return false;

                const parts = (timer.textContent || "")
                    .trim()
                    .split(":")
                    .map(Number);
                if (
                    (parts.length !== 2 && parts.length !== 3)
                    || parts.some(Number.isNaN)
                ) return false;
                const actualSeconds = parts.length === 2
                    ? parts[0] * 60 + parts[1]
                    : parts[0] * 3600
                        + parts[1] * 60
                        + parts[2];
                if (actualSeconds <= 0) return false;

                const expectedSpeed = Math.min(
                    999,
                    Math.round(
                        100 * durationSeconds / actualSeconds
                    )
                );
                const speedMatch = (
                    speedSpan.textContent || ""
                ).match(/-?\d+/);
                if (
                    !speedMatch
                    || Math.abs(
                        Number(speedMatch[0]) - expectedSpeed
                    ) > 1
                ) return false;

                const baseMatch = (
                    points.textContent || ""
                ).match(/(\d+)\s*бал/i);
                if (!baseMatch) return false;
                let displayedPoints = Number(baseMatch[1]);
                for (
                    const span
                    of points.querySelectorAll("span")
                ) {
                    const adjustment = (
                        span.textContent || ""
                    ).match(/([+-])\s*(\d+)/);
                    if (!adjustment) continue;
                    const value = Number(adjustment[2]);
                    displayedPoints += adjustment[1] === "+"
                        ? value
                        : -value;
                }
                const speedBonus = actualSeconds < durationSeconds
                    ? Math.round(basePoints * 0.20)
                    : 0;
                return displayedPoints
                    === basePoints + speedBonus;
            }
            """,
            arg={
                "taskId": task_id,
                "durationSeconds": duration_seconds,
                "basePoints": base_points,
            },
            timeout=settings.CTF_METRICS_TIMEOUT_MS,
        )
        return await self._read_task_metrics(task)

    async def _read_task_metrics(
        self,
        task: Locator,
    ) -> TaskMetricsSnapshot:
        points_container = task.locator(".scenario-tasks-card-points").first
        await expect(points_container).to_be_visible(
            timeout=settings.CTF_METRICS_TIMEOUT_MS
        )
        points_text = (await points_container.inner_text()).strip()
        base_match = re.search(
            r"(\d+)\s*бал",
            points_text,
            re.IGNORECASE,
        )
        if base_match is None:
            raise AssertionError(f"Не удалось прочитать баллы: {points_text!r}")
        base_points = int(base_match.group(1))
        adjustments: list[int] = []
        spans = points_container.locator("span")
        for index in range(await spans.count()):
            text = (await spans.nth(index).inner_text()).strip()
            match = re.search(r"([+\-])\s*(\d+)", text)
            if match is None:
                continue
            value = int(match.group(2))
            adjustments.append(value if match.group(1) == "+" else -value)

        return TaskMetricsSnapshot(
            base_points=base_points,
            adjustments=tuple(adjustments),
            displayed_points=base_points + sum(adjustments),
            elapsed_seconds=await self.read_task_elapsed_seconds(task),
            accuracy=await self._read_dom_metric(
                self._metric_span_by_icon(
                    task,
                    "accuracy-grey.svg",
                )
            ),
            speed=await self._read_dom_metric(
                self._metric_span_by_icon(
                    task,
                    "speed-one-grey.svg",
                )
            ),
        )

    async def _wait_for_scenario_metrics(
        self,
        *,
        expected: AggregateMetricsExpectation,
    ) -> ScenarioMetricsSnapshot:
        container = self.page.locator(".scenarios-tasks-metric-container:visible").first
        await expect(container).to_be_visible(timeout=settings.CTF_METRICS_TIMEOUT_MS)
        values = (
            (
                "accuracy.svg",
                expected.accuracy_percent,
                True,
                0,
            ),
            (
                "speed-one.svg",
                expected.speed_percent,
                False,
                1,
            ),
            ("trophy.svg", expected.points, False, 0),
        )
        for icon, value, percent, tolerance in values:
            allowed = "|".join(
                str(candidate)
                for candidate in range(
                    max(0, value - tolerance),
                    value + tolerance + 1,
                )
            )
            suffix = r"\s*%" if percent else ""
            await expect(self._metric_span_by_icon(container, icon)).to_have_text(
                re.compile(rf"^\s*(?:{allowed}){suffix}\s*$"),
                timeout=settings.CTF_METRICS_TIMEOUT_MS,
            )

        return ScenarioMetricsSnapshot(
            accuracy=await self._read_dom_metric(
                self._metric_span_by_icon(
                    container,
                    "accuracy.svg",
                )
            ),
            speed=await self._read_dom_metric(
                self._metric_span_by_icon(
                    container,
                    "speed-one.svg",
                )
            ),
            points=await self._read_dom_metric(
                self._metric_span_by_icon(
                    container,
                    "trophy.svg",
                )
            ),
        )

    def _metric_span_by_icon(
        self,
        scope: Locator,
        icon_name: str,
    ) -> Locator:
        image = scope.locator(f"img[src$='{icon_name}']").first
        return image.locator("xpath=..").locator("span").first

    async def _read_dom_metric(
        self,
        span: Locator,
    ) -> DomMetricValue:
        await expect(span).to_be_visible(timeout=settings.CTF_METRICS_TIMEOUT_MS)
        text = (await span.inner_text()).strip()
        match = re.search(r"-?\d+", text)
        if match is None:
            raise AssertionError(f"Не удалось прочитать DOM-метрику: {text!r}")
        return DomMetricValue(value=int(match.group()))

    async def _read_timer_seconds(
        self,
        timer: Locator,
        *,
        timer_name: str,
        timeout_ms: int = settings.CTF_METRICS_TIMEOUT_MS,
    ) -> int:
        await expect(timer).to_be_visible(timeout=timeout_ms)
        text = (await timer.inner_text()).strip()
        parts = text.split(":")
        if len(parts) not in {2, 3} or any(not part.isdigit() for part in parts):
            raise AssertionError(f"Некорректный DOM-{timer_name}: {text!r}")
        values = [int(part) for part in parts]
        if len(values) == 2:
            return values[0] * 60 + values[1]
        return values[0] * 3600 + values[1] * 60 + values[2]

    async def _ensure_question_expanded(
        self,
        task: Locator,
    ) -> None:
        deadline = time.monotonic() + settings.CTF_TRANSITION_TIMEOUT_MS / 1_000
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            await self._handle_timer_warning()
            if (
                await self.answer_field(task).is_visible()
                and await self.elapsed_timer(task).is_visible()
            ):
                return

            try:
                chevron = task.locator(
                    "[kbq-icon='kbq-chevron-down-s_24']:visible"
                ).first
                target = chevron.locator("xpath=..").first
                if not await target.is_visible():
                    target = chevron if await chevron.is_visible() else task
                await safe_click(target, timeout=5_000)
                await self._wait_for_question_dom(task)
                await self._handle_timer_warning()
                if (
                    await self.answer_field(task).is_visible()
                    and await self.elapsed_timer(task).is_visible()
                ):
                    return
            except Exception as exc:
                last_error = exc

        raise AssertionError(
            f"{self._position_label()}: карточка не раскрылась"
        ) from last_error

    async def _question_is_expanded_and_ready(self, task: Locator) -> bool:
        try:
            return (
                await self.answer_field(task).is_visible()
                and await self.answer_field(task).is_enabled()
                and await self.elapsed_timer(task).is_visible()
            )
        except Exception:
            return False

    async def _wait_for_question_dom(
        self,
        task: Locator,
    ) -> None:
        task_id = await task.get_attribute("id")
        if not task_id:
            await task.evaluate(
                """
                (element) => new Promise((resolve, reject) => {
                    const visible = (node) => Boolean(
                        node
                        && getComputedStyle(node).visibility !== "hidden"
                        && getComputedStyle(node).display !== "none"
                        && node.getClientRects().length
                    );
                    const ready = () => {
                        const warning = document.querySelector(
                            "matrix-timer-warning-dialog"
                        );
                        const answer = element.querySelector(
                            "input[kbqinput], input[type='text'], input"
                        );
                        const timer = element.querySelector(
                            ".height-30 "
                            + "span.scenario-tasks-card__timer"
                            + ":not(.mx-color-grey)"
                        );
                        return visible(warning)
                            || (visible(answer) && visible(timer));
                    };
                    if (ready()) {
                        resolve(true);
                        return;
                    }
                    const observer = new MutationObserver(() => {
                        if (ready()) {
                            observer.disconnect();
                            resolve(true);
                        }
                    });
                    observer.observe(element, {
                        attributes: true,
                        childList: true,
                        subtree: true
                    });
                    const timer = setTimeout(() => {
                        observer.disconnect();
                        reject(new Error("question dom timeout"));
                    }, 5000);
                })
                """
            )
            return
        await self.page.wait_for_function(
            """
            (taskId) => {
                const visible = (element) => Boolean(
                    element
                    && getComputedStyle(element).visibility !== "hidden"
                    && getComputedStyle(element).display !== "none"
                    && element.getClientRects().length
                );
                const task = document.getElementById(taskId);
                const warning = document.querySelector(
                    "matrix-timer-warning-dialog"
                );
                const answer = task?.querySelector(
                    "input[kbqinput], input[type='text'], input"
                );
                const timer = task?.querySelector(
                    ".height-30 "
                    + "span.scenario-tasks-card__timer"
                    + ":not(.mx-color-grey)"
                );
                return visible(warning)
                    || (visible(answer) && visible(timer));
            }
            """,
            arg=task_id,
            timeout=5_000,
        )

    async def _handle_timer_warning(self) -> bool:
        dialog = self.page.locator("matrix-timer-warning-dialog:visible").first
        if not await dialog.is_visible():
            return False
        button = dialog.get_by_role(
            "button",
            name="Приступить к заданию",
        ).first
        await safe_click(button, timeout=5_000)
        await expect(dialog).to_be_hidden(timeout=settings.CTF_TRANSITION_TIMEOUT_MS)
        return True

    async def collapse_question(self, task: Locator) -> None:
        answer_field = self.answer_field(task)
        field_was_visible = await answer_field.is_visible()
        chevron = task.locator("[kbq-icon='kbq-chevron-up-s_24']:visible").first
        chevron_was_visible = await chevron.is_visible()
        active_state = task.locator(".scenario-tasks-card__active:visible").first
        active_state_was_visible = await active_state.is_visible()
        actions_were_visible = await self._task_open_actions(task).count() > 0
        if (
            not field_was_visible
            and not chevron_was_visible
            and not active_state_was_visible
            and not actions_were_visible
        ):
            return

        candidates = (
            chevron.locator("xpath=..").first,
            chevron,
            task.locator(".scenario-tasks-card-title-question:visible").first,
            task.locator(".scenario-tasks-card-title:visible").first,
            task,
        )
        for candidate in candidates:
            try:
                if not await candidate.is_visible():
                    continue
                await self._click_with_fallback(candidate)
                if await self._question_is_collapsed(
                    task,
                    timeout_ms=5_000,
                ):
                    log_step(
                        f"[CTF][STAGE {self.current_stage}]"
                        f"[Q{self.current_question}] "
                        "Карточка вопроса закрыта по DOM"
                    )
                    return
            except Exception:
                continue

        raise AssertionError(
            f"{self._position_label()}: не удалось закрыть карточку задания"
        )

    async def _collapse_open_question(self) -> None:
        cards_count = await self.visible_task_cards().count()
        for _ in range(cards_count + 1):
            expanded = self._expanded_task_cards()
            if await expanded.count() == 0:
                return
            await self.collapse_question(expanded.first)

        if await self._expanded_task_cards().count() > 0:
            raise AssertionError(
                f"{self._position_label()}: не удалось закрыть все раскрытые карточки"
            )

    def _expanded_task_cards(self) -> Locator:
        return self.page.locator(
            "[id^='matrix_scenarios_task_']:visible"
            ":has(input[kbqinput]:visible), "
            "[id^='matrix_scenarios_task_']:visible"
            ":has(input[type='text']:visible), "
            "[id^='matrix_scenarios_task_']:visible"
            ":has(.scenario-tasks-card__active:visible), "
            "[id^='matrix_scenarios_task_']:visible"
            ":has([kbq-icon='kbq-chevron-up-s_24']:visible), "
            "[id^='matrix_scenarios_task_']:visible"
            ":has(button#send-btn:visible), "
            "[id^='matrix_scenarios_task_']:visible"
            ":has(button#hint-btn:visible), "
            "[id^='matrix_scenarios_task_']:visible"
            ":has(button#answer-btn:visible), "
            ".scenario-tasks-card:visible"
            ":has(input[kbqinput]:visible), "
            ".scenario-tasks-card:visible"
            ":has(input[type='text']:visible), "
            ".scenario-tasks-card:visible"
            ":has(.scenario-tasks-card__active:visible), "
            ".scenario-tasks-card:visible"
            ":has([kbq-icon='kbq-chevron-up-s_24']:visible), "
            ".scenario-tasks-card:visible"
            ":has(button#send-btn:visible), "
            ".scenario-tasks-card:visible"
            ":has(button#hint-btn:visible), "
            ".scenario-tasks-card:visible"
            ":has(button#answer-btn:visible)"
        )

    def _task_open_actions(self, task: Locator) -> Locator:
        return task.locator(
            "button#send-btn:visible, "
            "button#hint-btn:visible, "
            "button#answer-btn:visible"
        )

    async def _question_is_collapsed(
        self,
        task: Locator,
        *,
        timeout_ms: int,
    ) -> bool:
        try:
            await expect(self.answer_field(task)).to_be_hidden(timeout=timeout_ms)
            await expect(
                task.locator("[kbq-icon='kbq-chevron-up-s_24']").first
            ).to_be_hidden(timeout=timeout_ms)
            await expect(
                task.locator(".scenario-tasks-card__active").first
            ).to_be_hidden(timeout=timeout_ms)
            await expect(self._task_open_actions(task)).to_have_count(
                0, timeout=timeout_ms
            )
            return True
        except Exception:
            return False

    async def _wait_for_timer_tick(
        self,
        task: Locator,
    ) -> None:
        task_id = await task.get_attribute("id")
        if not task_id:
            return
        start_mark = self._question_start_marks.get(
            self._question_key(),
            0,
        )
        await self.page.wait_for_function(
            """
            ([selector, startMark]) => {
                const root = document.querySelector(selector);
                if (!root) return false;
                const timer = root.querySelector(
                    ".height-30 "
                    + "span.scenario-tasks-card__timer"
                    + ":not(.mx-color-grey)"
                );
                return Boolean(timer)
                    && performance.now() - startMark >= 1200;
            }
            """,
            arg=[f"#{task_id}", start_mark],
            timeout=5_000,
        )

    async def _resolve_send_button(
        self,
        task: Locator,
    ) -> Locator:
        candidates = (
            task.locator("button#send-btn:visible").first,
            task.get_by_role(
                "button",
                name=re.compile(
                    r"отправ|submit|answer",
                    re.IGNORECASE,
                ),
            ).first,
            task.locator("button[kbqtooltipclass]:visible").last,
        )
        for candidate in candidates:
            if await candidate.count() > 0 and await candidate.is_visible():
                return candidate
        raise AssertionError(f"{self._position_label()}: кнопка отправки не найдена")

    def _stage_continue_button(self) -> Locator:
        return (
            self.page.locator(
                "button#congratulationModalSubmit:visible, button:visible"
            )
            .filter(
                has_text=re.compile(
                    r"^\s*Продолжить\s*$",
                    re.IGNORECASE,
                )
            )
            .last
        )

    async def _read_visible_product_interval_text(
        self,
    ) -> ProductInterval | None:
        selectors = (
            "[id^='matrix_scenarios_task_']:visible",
            ".scenario-tasks-card:visible",
            "kbq-tab-body.kbq-tab-body__active:visible",
            "matrix-home:visible",
        )
        for selector in selectors:
            texts = await self.page.locator(selector).all_inner_texts()
            for text in texts:
                interval = self._parse_product_interval_text(text)
                if interval is not None:
                    return interval
        return None

    async def _read_product_interval_from_link(
        self,
    ) -> ProductInterval | None:
        link = self.product_link()
        try:
            await expect(link).to_be_visible(timeout=2_000)
        except Exception:
            return None

        href = (await link.get_attribute("href")) or ""
        parsed = urlsplit(href)
        fragment_query = urlsplit(parsed.fragment).query
        query = parse_qs(fragment_query or parsed.query)
        start_ms = self._timestamp_query_value(
            query,
            "from",
            "start",
            "startTime",
        )
        end_ms = self._timestamp_query_value(
            query,
            "to",
            "end",
            "endTime",
        )
        if start_ms is None or end_ms is None:
            return None

        return ProductInterval(
            start_date=self._format_product_date(start_ms),
            start_time=self._format_product_time(start_ms),
            end_date=self._format_product_date(end_ms),
            end_time=self._format_product_time(end_ms),
        )

    def _parse_sandbox_product_interval_href(
        self,
        href: str,
    ) -> ProductInterval | None:
        parsed = urlsplit(href)
        query = parse_qs(parsed.query)
        raw_filters = query.get("filters", [""])[0]
        if not raw_filters:
            return None

        try:
            filters = json.loads(raw_filters)
            time_range = filters["time"]["range"]
            start_ms = int(time_range["startTimestamp"])
            end_ms = int(time_range["endTimestamp"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

        return ProductInterval(
            start_date=self._format_product_date(start_ms),
            start_time=self._format_product_time(start_ms),
            end_date=self._format_product_date(end_ms),
            end_time=self._format_product_time(end_ms),
        )

    @staticmethod
    def _timestamp_query_value(
        query: dict[str, list[str]],
        *keys: str,
    ) -> int | None:
        for key in keys:
            try:
                return int(query[key][0])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        return None

    def _parse_product_interval_text(
        self,
        text: str,
    ) -> ProductInterval | None:
        pattern = re.compile(
            r"Временной интервал.*?"
            r"(\d{2}\.\d{2}\.\d{4})\s+"
            r"(\d{2}:\d{2})(?::\d{2})?\s*-\s*"
            r"(\d{2}\.\d{2}\.\d{4})\s+"
            r"(\d{2}:\d{2})(?::\d{2})?",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(text)
        if match is None:
            return None
        return ProductInterval(
            start_date=match.group(1),
            start_time=match.group(2),
            end_date=match.group(3),
            end_time=match.group(4),
        )

    def _format_product_date(self, timestamp_ms: int) -> str:
        return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%d.%m.%Y")

    def _format_product_time(self, timestamp_ms: int) -> str:
        return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%H:%M")

    async def _ensure_actionbar_open(self) -> None:
        actionbar = self.page.locator("matrix-actionbar:visible").first
        if await actionbar.is_visible(timeout=500):
            return

        expand = (
            self.page.locator(".basic-scenario-actionbar-show:visible")
            .filter(
                has_text=re.compile(
                    r"Развернуть",
                    re.IGNORECASE,
                )
            )
            .first
        )

        try:
            await self._click_with_fallback(expand)
        except Exception:
            await self._click_with_fallback(
                self.page.get_by_text("Развернуть", exact=True).first
            )

        await expect(actionbar).to_be_visible(
            timeout=settings.CTF_TRANSITION_TIMEOUT_MS
        )
        log_step(
            f"[CTF][STAGE {self.current_stage}][ACTIONBAR] "
            "Боковая панель раскрыта по DOM"
        )

    async def _ensure_actionbar_open_from_inline_scheme(self) -> None:
        inline_scheme = self._actionbar_inline_scheme_image()
        if await inline_scheme.is_visible(timeout=1_000):
            return

        right_container = self._actionbar_right_container()
        if await right_container.is_visible(timeout=500):
            await expect(inline_scheme).to_be_visible(
                timeout=settings.CTF_MATERIAL_TIMEOUT_MS
            )
            await self._wait_for_image_loaded(inline_scheme)
            return

        expand = (
            self.page.locator(".basic-scenario-actionbar-show:visible")
            .filter(
                has_text=re.compile(
                    r"Развернуть",
                    re.IGNORECASE,
                )
            )
            .first
        )

        try:
            await self._click_with_fallback(expand)
        except Exception:
            await self._click_with_fallback(
                self.page.get_by_text("Развернуть", exact=True).first
            )

        await expect(inline_scheme).to_be_visible(
            timeout=settings.CTF_TRANSITION_TIMEOUT_MS
        )
        await self._wait_for_image_loaded(inline_scheme)
        log_step(
            f"[CTF][STAGE {self.current_stage}][ACTIONBAR] "
            "Боковая панель раскрыта по DOM"
        )

    def _actionbar_right_container(self) -> Locator:
        return self.page.locator(".scenarios-result__right-container:visible").first

    def _actionbar_inline_scheme_image(self) -> Locator:
        return (
            self._actionbar_right_container()
            .locator(".scenarios-result__scheme-container:visible img[src]:visible")
            .first
        )

    def _stage_completion_material_link(
        self,
        *,
        text: re.Pattern[str],
        final_stage: bool,
    ) -> Locator:
        if final_stage:
            dialog = self.page.locator("matrix-congratulation:visible").last
        else:
            dialog_text = self.page.get_by_text(
                re.compile(
                    r"Отличная работа|"
                    r"Вы успешно выполнили все задания Этапа|"
                    r"Этапы атаки",
                    re.IGNORECASE,
                )
            )
            dialog = (
                self.page.locator(
                    "[role='dialog']:visible, "
                    ".cdk-overlay-pane:visible, "
                    "[class*='modal']:visible, "
                    "[class*='dialog']:visible"
                )
                .filter(has=dialog_text)
                .last
            )

        return (
            dialog.locator("span.statistics-link-cursor:visible")
            .filter(has_text=text)
            .last
        )

    async def _wait_for_completion_parent(
        self,
        *,
        final_stage: bool,
    ) -> None:
        if final_stage:
            await self.verify_final_dialog()
            return

        await expect(self._stage_continue_button()).to_be_visible(
            timeout=settings.CTF_MATERIAL_TIMEOUT_MS
        )

    async def _click_stage_completion_video_link(
        self,
        *,
        final_stage: bool,
        close: Locator,
    ) -> None:
        scoped_link = self._stage_completion_material_link(
            text=re.compile(
                r"Видео расследования|Видео",
                re.IGNORECASE,
            ),
            final_stage=final_stage,
        )
        try:
            await expect(scoped_link).to_be_visible(
                timeout=settings.CTF_MATERIAL_TIMEOUT_MS
            )
            await self._click_with_fallback(scoped_link)
            if await self._wait_for_video_modal(
                close,
                timeout_ms=3_000,
            ):
                return
        except Exception:
            pass

        fallback = (
            self.page.locator("span.statistics-link-cursor:visible")
            .filter(has_text=re.compile(r"Видео", re.IGNORECASE))
            .last
        )
        try:
            await self._click_with_fallback(fallback)
            if await self._wait_for_video_modal(
                close,
                timeout_ms=3_000,
            ):
                return
        except Exception:
            pass

        await self._click_visible_statistics_video_link()
        await expect(close).to_be_visible(timeout=settings.CTF_MATERIAL_TIMEOUT_MS)

    async def _click_visible_statistics_video_link(self) -> None:
        await self.page.evaluate(
            """
            () => {
                const visible = (element) => {
                    const style = getComputedStyle(element);
                    return style.visibility !== "hidden"
                        && style.display !== "none"
                        && (
                            element.offsetWidth > 0
                            || element.offsetHeight > 0
                            || element.getClientRects().length > 0
                        );
                };
                const links = Array.from(
                    document.querySelectorAll(
                        "span.statistics-link-cursor"
                    )
                ).filter((element) => (
                    visible(element)
                    && /Видео/i.test(element.textContent || "")
                ));
                const target = links[links.length - 1];
                if (!target) {
                    throw new Error(
                        "Video investigation link is not visible"
                    );
                }
                target.scrollIntoView({
                    block: "center",
                    inline: "center"
                });
                target.click();
            }
            """
        )

    async def _wait_for_video_modal(
        self,
        close: Locator,
        *,
        timeout_ms: int,
    ) -> bool:
        try:
            await expect(close).to_be_visible(timeout=timeout_ms)
            return True
        except Exception:
            return False

    async def _wait_for_stage_video_present(self) -> None:
        await self.page.wait_for_function(
            """
            () => Array.from(
                document.querySelectorAll("video")
            ).some((video) => {
                const source = video.querySelector("source");
                const src = video.currentSrc
                    || video.src
                    || video.getAttribute("src")
                    || source?.src
                    || source?.getAttribute("src")
                    || "";
                const style = getComputedStyle(video);
                const visible = style.visibility !== "hidden"
                    && style.display !== "none"
                    && (
                        video.offsetWidth > 0
                        || video.offsetHeight > 0
                        || video.getClientRects().length > 0
                    );
                const expectedSource = src.includes("/media/doc/")
                    || src.includes("/video/");
                return visible || (
                    expectedSource
                    && video.readyState
                        >= HTMLMediaElement.HAVE_METADATA
                );
            })
            """,
            timeout=settings.CTF_MATERIAL_TIMEOUT_MS,
        )

    async def _close_modal_by_button(
        self,
        button: Locator,
    ) -> None:
        if await self._material_modal_is_closed(timeout_ms=300):
            return

        candidates = (
            button,
            self.page.locator("button.kbq-modal-close:visible").last,
            self.page.get_by_role(
                "button",
                name=re.compile(
                    r"Закрыть|Close|ОК|OK",
                    re.IGNORECASE,
                ),
            ).last,
        )
        last_error: Exception | None = None
        for target in candidates:
            try:
                if not await target.is_visible(timeout=500):
                    continue
                await self._click_with_fallback(target)
                if await self._material_modal_is_closed(timeout_ms=5_000):
                    return
            except Exception as exc:
                last_error = exc

        try:
            await self.page.keyboard.press("Escape")
            if await self._material_modal_is_closed(timeout_ms=2_000):
                return
        except Exception as exc:
            last_error = exc

        if last_error is not None:
            raise AssertionError(
                "Модальное окно материалов CTF не закрылось"
            ) from last_error
        raise AssertionError("Модальное окно материалов CTF не закрылось")

    async def _material_modal_is_closed(
        self,
        *,
        timeout_ms: int,
    ) -> bool:
        try:
            await self.page.wait_for_function(
                """
                () => {
                    const visible = (element) => {
                        const style = window.getComputedStyle(element);
                        const rect = element.getBoundingClientRect();
                        return (
                            style.display !== "none"
                            && style.visibility !== "hidden"
                            && rect.width > 0
                            && rect.height > 0
                            && element.getClientRects().length > 0
                        );
                    };
                    const selectors = [
                        "button#reportModalSubmit",
                        "button#videoModalCloseBtn",
                        ".scheme-dialog-container__image",
                        ".scheme-dialog-container__second-description",
                        "video"
                    ];
                    return !selectors.some((selector) => (
                        Array.from(
                            document.querySelectorAll(selector)
                        ).some(visible)
                    ));
                }
                """,
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    async def _click_final_feedback_submit(
        self,
        button: Locator,
    ) -> None:
        wrapper = button.locator(".kbq-button-wrapper").first
        overlay = button.locator(".kbq-button-overlay").first
        candidates = (button, wrapper, overlay)
        last_error: Exception | None = None
        for candidate in candidates:
            try:
                if not await candidate.is_visible(timeout=500):
                    continue
                await candidate.click(timeout=3_000, force=True)
                return
            except Exception as exc:
                last_error = exc

        try:
            await button.evaluate(
                """
                (element) => {
                    element.click();
                    element.dispatchEvent(new MouseEvent(
                        "mousedown",
                        { bubbles: true, cancelable: true }
                    ));
                    element.dispatchEvent(new MouseEvent(
                        "mouseup",
                        { bubbles: true, cancelable: true }
                    ));
                    element.dispatchEvent(new MouseEvent(
                        "click",
                        { bubbles: true, cancelable: true }
                    ));
                }
                """
            )
        except Exception as exc:
            raise AssertionError("Кнопка «Отправить» финального окна недоступна") from (
                last_error or exc
            )

    async def _wait_for_final_dialog_hidden(
        self,
        *,
        timeout_ms: int,
    ) -> bool:
        try:
            await expect(
                self.page.locator("matrix-congratulation:visible").last
            ).to_be_hidden(timeout=timeout_ms)
            return True
        except Exception:
            return False

    async def _close_final_dialog(
        self,
        dialog: Locator,
    ) -> bool:
        if await dialog.count() == 0 or not await dialog.is_visible():
            return True

        close_icon = dialog.locator(
            "i[kbq-icon='kbq-xmark_16'].close-btn:visible, "
            "i[kbq-icon='kbq-xmark_16']:visible, "
            ".kbq.kbq-icon.close-btn:visible"
        ).last
        candidates = (
            close_icon,
            close_icon.locator("xpath=..").first,
            dialog.locator("button.kbq-modal-close:visible").last,
        )
        for candidate in candidates:
            try:
                if not await candidate.is_visible(timeout=500):
                    continue
                await candidate.click(timeout=1_000, force=True)
                if await self._wait_for_final_dialog_hidden(timeout_ms=2_000):
                    return True
            except Exception:
                continue

        try:
            await close_icon.evaluate("(element) => element.click()")
            return await self._wait_for_final_dialog_hidden(timeout_ms=2_000)
        except Exception:
            return False

    async def _wait_for_image_loaded(
        self,
        image: Locator,
    ) -> None:
        handle = await image.element_handle()
        if handle is None:
            raise AssertionError("Изображение не найдено")
        await self.page.wait_for_function(
            """
            (element) => (
                element.complete
                && element.naturalWidth > 0
                && element.naturalHeight > 0
            )
            """,
            arg=handle,
            timeout=settings.CTF_MATERIAL_TIMEOUT_MS,
        )

    async def _click_with_fallback(
        self,
        locator: Locator,
    ) -> None:
        if await locator.count() == 0:
            raise AssertionError("DOM-элемент для клика не найден")
        try:
            await safe_click(locator, timeout=3_000)
            return
        except Exception:
            pass

        try:
            await locator.click(force=True, timeout=3_000)
            return
        except Exception:
            pass

        try:
            await locator.evaluate("(element) => element.click()")
            return
        except Exception:
            pass

        child = locator.locator(
            "div.kbq-button-wrapper, div.kbq-button-overlay, svg, i, span"
        ).first
        await child.click(force=True, timeout=3_000)

    def _question_key(self) -> str:
        return f"{self.current_stage}:{self.current_question}"

    def _position_label(self) -> str:
        return f"STAGE {self.current_stage} Q{self.current_question}"
