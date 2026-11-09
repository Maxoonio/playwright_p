from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import settings
from playwright.async_api import Locator, Page, Response, expect
from utils.logging import (
    compact_exception_message,
    log_step,
)
from utils.network import (
    perform_and_wait_for_dom_with_network_observation,
    perform_and_wait_for_response,
    response_path_is,
    same_origin_content_response,
)
from utils.ui import safe_click


@dataclass(frozen=True)
class IntroductionLinkTarget:
    action: str
    link_text: str
    onclick_fragment: str
    document_name: str
    target_title: str
    target_step_id: str | None = None
    parent_section_id: str | None = None
    parent_section_title: str | None = None


WEB_INTERFACE_TARGET = IntroductionLinkTarget(
    action="WEB_INTERFACE_LINK_CONFIRMED",
    link_text="Веб-интерфейс продукта",
    onclick_fragment="stepClick('11560197387_ru.html')",
    document_name="11560197387_ru.html",
    target_title="Главное меню",
    target_step_id="scenario_step_id_5",
    parent_section_id="id_2",
    parent_section_title="Веб-интерфейс PT Sandbox",
)

MALWARE_CAPABILITIES_TARGET = IntroductionLinkTarget(
    action="MALWARE_CAPABILITIES_LINK_CONFIRMED",
    link_text="Возможности обнаружения ВПО",
    onclick_fragment="stepClick('11583685387_ru.html')",
    document_name="11583685387_ru.html",
    target_title="Статический анализ",
    target_step_id="scenario_step_id_17",
    parent_section_id="id_3",
    parent_section_title="Возможности по обнаружению ВПО",
)

USE_CASES_TARGET = IntroductionLinkTarget(
    action="USE_CASES_LINK_CONFIRMED",
    link_text="Сценарии использования",
    onclick_fragment="stepClick('11583691147_ru.html')",
    document_name="11583691147_ru.html",
    target_title="Анализ письма с вредоносным архивом",
    target_step_id="scenario_step_id_21",
    parent_section_id="id_4",
    parent_section_title="Сценарии использования",
)

COMPLEX_SCENARIO_TARGET = IntroductionLinkTarget(
    action="COMPLEX_SCENARIO_LINK_CONFIRMED",
    link_text="Комплексный сценарий",
    onclick_fragment="stepClick('33_common_td_complex_ru.html')",
    document_name="33_common_td_complex_ru.html",
    target_title="Комплексный сценарий",
    target_step_id="scenario_step_id_161",
)

INTRODUCTION_LINK_TARGETS = (
    WEB_INTERFACE_TARGET,
    MALWARE_CAPABILITIES_TARGET,
    USE_CASES_TARGET,
    COMPLEX_SCENARIO_TARGET,
)

INTRODUCTION_DOM_LINKS = (
    (
        WEB_INTERFACE_TARGET.link_text,
        WEB_INTERFACE_TARGET.onclick_fragment,
    ),
    (
        MALWARE_CAPABILITIES_TARGET.link_text,
        MALWARE_CAPABILITIES_TARGET.onclick_fragment,
    ),
    (
        USE_CASES_TARGET.link_text,
        USE_CASES_TARGET.onclick_fragment,
    ),
    (
        COMPLEX_SCENARIO_TARGET.link_text,
        COMPLEX_SCENARIO_TARGET.onclick_fragment,
    ),
    (
        WEB_INTERFACE_TARGET.link_text,
        WEB_INTERFACE_TARGET.onclick_fragment,
    ),
    (
        "Возможности по обнаружению ВПО",
        MALWARE_CAPABILITIES_TARGET.onclick_fragment,
    ),
    (
        USE_CASES_TARGET.link_text,
        USE_CASES_TARGET.onclick_fragment,
    ),
)

CTF_REQUIRED_MODULES = (
    "Страница Объекты",
    "Страница Источники",
    "Статический анализ",
    "Поведенческий анализ",
    "Анализ письма с вредоносным архивом",
)

CTF_REQUIRED_MODULE_STEP_IDS = (8, 10, 17, 18, 21)


@dataclass(frozen=True)
class SosSubmissionResult:
    request_ok: bool
    dialog_closed: bool
    response_status: int | None
    ticket_number: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ProSubmissionResult:
    attempts: int
    response_status: int | None
    dialog_closed: bool


class ProRequestHttpError(AssertionError):
    """PRO API rejected the request; repeating the same payload is pointless."""


class SandboxTestDriveScenarioPage:
    INTRO_SECTION_ID = "id_1"
    ABOUT_TEST_DRIVE_STEP_ID = "scenario_step_id_1"
    ABOUT_MATRIX_STEP_ID = "scenario_step_id_2"
    ABOUT_SANDBOX_STEP_ID = "scenario_step_id_3"

    def __init__(self, page: Page) -> None:
        self.page = page
        self._test_drive_finish_submitted = False
        self._final_feedback_dialog_opened = False

    @property
    def test_drive_finish_submitted(self) -> bool:
        return self._test_drive_finish_submitted

    @property
    def final_feedback_dialog_opened(self) -> bool:
        return self._final_feedback_dialog_opened

    def section(self, section_id: str) -> Locator:
        return self.page.locator(f"#{section_id}")

    def sidebar_step(self, step_id: str) -> Locator:
        return self.page.locator(f"#{step_id}")

    def _step_title(self, step: Locator, title: str) -> Locator:
        return (
            step.locator(".actionbar__step-title")
            .get_by_text(
                title,
                exact=True,
            )
            .first
        )

    def _visible_intro_link(
        self,
        *,
        text: str,
        onclick_fragment: str,
    ) -> Locator:
        escaped = onclick_fragment.replace('"', '\\"')
        # Учебный HTML загружается отдельным same-origin iframe. Локатор через
        # FrameLocator переживает замену iframe при переходе между модулями.
        return (
            self.page.frame_locator("iframe.scenarios-basic-result__iframe")
            .locator(f'a[onclick*="{escaped}"]:visible')
            .filter(has_text=re.compile(rf"^\s*{re.escape(text)}\s*$", re.IGNORECASE))
            .first
        )

    def _scenario_response(self, response: Response) -> bool:
        return same_origin_content_response(
            response,
            base_url=settings.BASE_URL,
        )

    async def open(self) -> None:
        response = await self.page.goto(
            settings.SANDBOX_C1_SCENARIO_URL,
            wait_until="domcontentloaded",
            timeout=settings.APP_START_TIMEOUT_MS,
        )
        if response is not None:
            path = urlsplit(response.url).path
            log_step(
                f"[NETWORK] Открытие Sandbox c-1: "
                f"{response.request.method} {path} -> {response.status}"
            )
            if not response.ok:
                raise AssertionError(
                    f"Страница сценария вернула HTTP {response.status}"
                )

        await expect(self.section(self.INTRO_SECTION_ID)).to_be_visible(
            timeout=settings.APP_START_TIMEOUT_MS
        )
        await expect(
            self.section(self.INTRO_SECTION_ID).get_by_text(
                "Введение",
                exact=True,
            )
        ).to_be_visible(timeout=settings.APP_START_TIMEOUT_MS)
        await expect(self.sidebar_step(self.ABOUT_TEST_DRIVE_STEP_ID)).to_be_visible(
            timeout=settings.APP_START_TIMEOUT_MS
        )
        await self.open_about_test_drive()
        log_step("[SCENARIO] Открыт PT Sandbox test-drive c-1")

    async def open_sidebar_step(
        self,
        *,
        step_id: str,
        title: str,
        ready_locator: Locator | None = None,
    ) -> None:
        step = self.sidebar_step(step_id)
        if not await step.is_visible():
            await safe_click(self.section(self.INTRO_SECTION_ID))
            await expect(step).to_be_visible()

        async def wait_for_target_dom() -> None:
            await expect(step).to_be_visible()
            await expect(self._step_title(step, title)).to_be_visible()
            await expect(step).to_have_class(
                re.compile(r"\bstep_active\b"),
                timeout=settings.DOM_TIMEOUT_MS,
            )
            if ready_locator is not None:
                await expect(ready_locator).to_be_visible()

        step_classes = (await step.get_attribute("class")) or ""
        if (
            ready_locator is not None
            and await ready_locator.is_visible()
            and "step_active" in step_classes.split()
        ):
            await expect(self._step_title(step, title)).to_be_visible()
            return

        await perform_and_wait_for_dom_with_network_observation(
            self.page,
            lambda: safe_click(step),
            wait_for_target_dom,
            predicate=self._scenario_response,
            description=f"Открытие подраздела «{title}»",
        )
        log_step(f"[INTRO][STEP] Открыт подраздел «{title}»")

    async def open_about_test_drive(self) -> None:
        link = self._visible_intro_link(
            text=WEB_INTERFACE_TARGET.link_text,
            onclick_fragment=WEB_INTERFACE_TARGET.onclick_fragment,
        )
        await self.open_sidebar_step(
            step_id=self.ABOUT_TEST_DRIVE_STEP_ID,
            title="О Тест-драйве",
            ready_locator=link,
        )

    async def open_about_matrix(self) -> None:
        video_link = self._visible_intro_link(
            text="обзорный видеотур",
            onclick_fragment="openDialogClick('videotour')",
        )
        await self.open_sidebar_step(
            step_id=self.ABOUT_MATRIX_STEP_ID,
            title="О PT Matrix",
            ready_locator=video_link,
        )

    async def open_about_sandbox(self) -> None:
        step = self.sidebar_step(self.ABOUT_SANDBOX_STEP_ID)
        if await step.count() == 0:
            step = (
                self.page.locator(".actionbar__step-container")
                .filter(
                    has=self.page.get_by_text(
                        "О PT Sandbox",
                        exact=True,
                    )
                )
                .first
            )

        async def wait_for_target_dom() -> None:
            await expect(step).to_be_visible()
            await expect(self._step_title(step, "О PT Sandbox")).to_be_visible()
            await expect(step).to_have_class(
                re.compile(r"\bstep_active\b"),
                timeout=settings.DOM_TIMEOUT_MS,
            )

        await perform_and_wait_for_dom_with_network_observation(
            self.page,
            lambda: safe_click(step),
            wait_for_target_dom,
            predicate=self._scenario_response,
            description="Открытие подраздела «О PT Sandbox»",
        )
        log_step("[INTRO][STEP] Открыт подраздел «О PT Sandbox»")

    async def verify_section_started(self, section_id: str) -> None:
        section = self.section(section_id)
        await expect(section).to_be_visible()
        await expect(
            section.locator(".actionbar__step__circle.actionbar__step__circle-yellow")
        ).to_be_visible()

    async def verify_step_completed(self, step_id: str) -> None:
        step = self.sidebar_step(step_id)
        await expect(step).to_be_visible()
        await expect(
            step.locator("img.actionbar__step[src*='success-filled.svg']")
        ).to_be_visible()

    async def verify_introduction_link(
        self,
        target: IntroductionLinkTarget,
    ) -> None:
        await self.open_about_test_drive()

        link = self._visible_intro_link(
            text=target.link_text,
            onclick_fragment=target.onclick_fragment,
        )
        await expect(link).to_be_visible()

        target_step = self._target_step(target)

        async def wait_for_target_dom() -> None:
            await expect(target_step).to_be_visible()
            await expect(
                self._step_title(target_step, target.target_title)
            ).to_be_visible()
            await expect(target_step).to_have_class(
                re.compile(r"\bstep_active\b"),
                timeout=settings.DOM_TIMEOUT_MS,
            )

            if target.parent_section_id is not None:
                parent = self.section(target.parent_section_id)
                await expect(parent).to_be_visible()
                await expect(
                    parent.get_by_text(
                        target.parent_section_title or "",
                        exact=True,
                    )
                ).to_be_visible()

            iframe = self.page.locator("iframe.scenarios-basic-result__iframe")
            await expect(iframe).to_have_attribute(
                "src",
                re.compile(rf"/{re.escape(target.document_name)}(?:[?#].*)?$"),
            )
            await expect(
                self.page.frame_locator(
                    "iframe.scenarios-basic-result__iframe"
                ).locator("body")
            ).to_be_visible()

        await perform_and_wait_for_response(
            self.page,
            lambda: safe_click(link),
            predicate=lambda response: (
                response.request.method.upper() == "GET"
                and urlsplit(response.url).path.endswith(f"/{target.document_name}")
            ),
            description=f"Переход по ссылке «{target.link_text}»",
        )
        await wait_for_target_dom()
        log_step(f"[INTRO][LINK] «{target.link_text}» -> «{target.target_title}»")
        await self.open_about_test_drive()

    async def verify_introduction_link_catalog(self) -> None:
        await self.open_about_test_drive()
        links = self.page.frame_locator(
            "iframe.scenarios-basic-result__iframe"
        ).locator("a[onclick]:visible")
        await expect(links).to_have_count(
            len(INTRODUCTION_DOM_LINKS),
            timeout=settings.DOM_TIMEOUT_MS,
        )

        actual: list[tuple[str, str]] = []
        for index in range(await links.count()):
            link = links.nth(index)
            text = re.sub(
                r"\s+",
                " ",
                (await link.inner_text()).replace("\xa0", " "),
            ).strip()
            onclick = (
                ((await link.get_attribute("onclick")) or "")
                .strip()
                .rstrip(";")
                .strip()
            )
            actual.append((text, onclick))

        expected = list(INTRODUCTION_DOM_LINKS)
        if actual != expected:
            raise AssertionError(
                "DOM-каталог ссылок «О тест-драйве» изменился: "
                f"ожидалось={expected!r}, получено={actual!r}"
            )
        log_step(
            "[INTRO][LINKS] DOM-каталог подтвержден: "
            + ", ".join(text for text, _ in actual)
        )

    async def open_introduction_link_target(
        self,
        target: IntroductionLinkTarget,
    ) -> None:
        await self.open_about_test_drive()
        link = self._visible_intro_link(
            text=target.link_text,
            onclick_fragment=target.onclick_fragment,
        )
        await expect(link).to_be_visible()

        target_step = self._target_step(target)

        async def wait_for_target_dom() -> None:
            await expect(target_step).to_be_visible()
            await expect(
                self._step_title(target_step, target.target_title)
            ).to_be_visible()
            await expect(target_step).to_have_class(
                re.compile(r"\bstep_active\b"),
                timeout=settings.DOM_TIMEOUT_MS,
            )

            if target.parent_section_id is not None:
                parent = self.section(target.parent_section_id)
                await expect(parent).to_be_visible()
                await expect(
                    parent.get_by_text(
                        target.parent_section_title or "",
                        exact=True,
                    )
                ).to_be_visible()

            iframe = self.page.locator("iframe.scenarios-basic-result__iframe")
            await expect(iframe).to_have_attribute(
                "src",
                re.compile(rf"/{re.escape(target.document_name)}(?:[?#].*)?$"),
            )
            await expect(
                self.page.frame_locator(
                    "iframe.scenarios-basic-result__iframe"
                ).locator("body")
            ).to_be_visible()

        await perform_and_wait_for_response(
            self.page,
            lambda: safe_click(link),
            predicate=lambda response: (
                response.request.method.upper() == "GET"
                and urlsplit(response.url).path.endswith(f"/{target.document_name}")
            ),
            description=f"Переход по ссылке «{target.link_text}»",
        )
        await wait_for_target_dom()
        log_step(f"[INTRO][LINK] «{target.link_text}» -> «{target.target_title}»")

    def _target_step(self, target: IntroductionLinkTarget) -> Locator:
        if target.target_step_id is not None:
            return self.sidebar_step(target.target_step_id)

        title = self.page.get_by_text(target.target_title, exact=True)
        return (
            self.page.locator(".actionbar__step-container.step_active")
            .filter(has=title)
            .first
        )

    async def verify_ctf_prerequisites_dialog(self) -> None:
        await self.open_about_test_drive()
        link = await self._visible_ctf_section()
        dialog = self.page.locator("matrix-ctf-table-dialog:visible").first

        async def wait_for_dialog() -> None:
            await expect(dialog).to_be_visible()
            await expect(
                dialog.get_by_text(re.compile(r"Поиск флага", re.IGNORECASE)).first
            ).to_be_visible()

        await perform_and_wait_for_dom_with_network_observation(
            self.page,
            lambda: safe_click(link),
            wait_for_dialog,
            predicate=self._scenario_response,
            description="Открытие окна подготовительных модулей CTF",
        )

        for module_name in CTF_REQUIRED_MODULES:
            await expect(dialog.get_by_text(module_name, exact=True)).to_be_visible()

        await expect(
            dialog.get_by_role(
                "button",
                name="Приступить к CTF сценарию",
            )
        ).to_be_visible()

        close_button = dialog.locator("button.kbq-modal-close:visible").first
        await safe_click(close_button)
        await expect(dialog).to_be_hidden()
        log_step(
            "[INTRO][CTF] Окно «Поиск флага» показано для "
            "непройденных подготовительных модулей и закрыто"
        )

    async def verify_video_tour(self) -> None:
        await self.open_about_matrix()
        link = self._visible_intro_link(
            text="обзорный видеотур",
            onclick_fragment="openDialogClick('videotour')",
        )
        player = self.page.locator("vg-player.video-container:visible").first
        video = player.locator("video[src='/media/manuals/videotour_ru.mp4']").first

        async def wait_for_video_dom() -> None:
            await expect(player).to_be_visible()
            await expect(video).to_be_visible()
            await expect(video).to_have_attribute(
                "src",
                "/media/manuals/videotour_ru.mp4",
            )
            await self.page.wait_for_function(
                """
                () => {
                    const video = document.querySelector(
                        "vg-player.video-container video"
                    );
                    return Boolean(video && video.readyState >= 1);
                }
                """,
                timeout=settings.DOM_TIMEOUT_MS,
            )

        close_button = self.page.locator("button#videoModalCloseBtn:visible").first
        try:
            await perform_and_wait_for_dom_with_network_observation(
                self.page,
                lambda: safe_click(link),
                wait_for_video_dom,
                predicate=lambda response: response_path_is(
                    response,
                    path="/media/manuals/videotour_ru.mp4",
                    method="GET",
                ),
                description="Загрузка обзорного видеотура",
            )
            log_step("[INTRO][VIDEO] Обзорный видеотур прогрузился")
        except Exception as exc:
            await expect(close_button).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
            log_step(
                "[INTRO][VIDEO] Видеотур не прогрузился полностью, "
                "закрываем окно и продолжаем: "
                f"{compact_exception_message(exc)}"
            )

        await safe_click(close_button)
        await expect(close_button).to_be_hidden(timeout=settings.DOM_TIMEOUT_MS)
        await expect(player).to_be_hidden(timeout=settings.DOM_TIMEOUT_MS)
        await expect(
            self.page.locator("kbq-progress-bar.actionbar__progress-bar:visible").first
        ).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        log_step("[INTRO][VIDEO] Обзорный видеотур закрыт")

    async def read_progress_percent(self) -> tuple[float, str]:
        progress_bar = self.page.locator(
            "kbq-progress-bar.actionbar__progress-bar:visible"
        ).first
        await expect(progress_bar).to_be_visible()
        container = progress_bar.locator("xpath=..")
        text = await container.inner_text()
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
        if match is None:
            line = progress_bar.locator(".kbq-progress-bar__line_determinate").first
            style = (await line.get_attribute("style")) or ""
            match = re.search(
                r"width\s*:\s*(\d+(?:\.\d+)?)%",
                style,
                re.IGNORECASE,
            )
            if match is None:
                raise AssertionError(f"Не удалось прочитать прогресс из DOM: {text!r}")

        progress_label = match.group(1)
        progress = float(progress_label.replace(",", "."))
        log_step(f"[PROGRESS] Текущий прогресс: {progress_label}%")
        return progress, progress_label

    async def wait_for_progress_above(
        self,
        previous_progress: float,
    ) -> tuple[float, str]:
        await self.page.wait_for_function(
            """
            (previous) => {
                const progressBar = document.querySelector(
                    "kbq-progress-bar.actionbar__progress-bar"
                );
                if (!progressBar) return false;
                const style = getComputedStyle(progressBar);
                if (
                    style.visibility === "hidden"
                    || style.display === "none"
                    || !progressBar.getClientRects().length
                ) return false;

                const containerText =
                    progressBar.parentElement?.innerText || "";
                let match = containerText.match(
                    /(\\d+(?:[,.]\\d+)?)\\s*%/
                );
                if (!match) {
                    const line = progressBar.querySelector(
                        ".kbq-progress-bar__line_determinate"
                    );
                    match = (line?.getAttribute("style") || "").match(
                        /width\\s*:\\s*(\\d+(?:\\.\\d+)?)%/i
                    );
                }
                if (!match) return false;
                return Number(match[1].replace(",", ".")) > previous;
            }
            """,
            arg=previous_progress,
            timeout=settings.DOM_TIMEOUT_MS,
        )
        return await self.read_progress_percent()

    async def verify_initial_finish_state(self) -> None:
        progress, progress_label = await self.read_progress_percent()
        button = self._finish_button_locator("Завершить тест-драйв")
        await expect(button).to_be_visible()
        if progress > settings.FINISH_PROGRESS_THRESHOLD:
            await expect(button).to_be_enabled()
            await expect(button).not_to_have_class(re.compile(r"\bkbq-disabled\b"))
            expected_state = "доступна"
        else:
            await expect(button).to_be_disabled()
            await expect(button).to_have_class(re.compile(r"\bkbq-disabled\b"))
            expected_state = "недоступна"
        log_step(
            f"[FINISH] При текущем прогрессе {progress_label}% кнопка "
            f"«Завершить тест-драйв» корректно {expected_state}"
        )

    async def verify_finish_available_after_introduction(self) -> float:
        try:
            await self.page.wait_for_function(
                """
                (threshold) => {
                    const visible = (element) => Boolean(
                        element
                        && getComputedStyle(element).visibility !== "hidden"
                        && getComputedStyle(element).display !== "none"
                        && element.getClientRects().length
                    );
                    const progressBar = document.querySelector(
                        "kbq-progress-bar.actionbar__progress-bar"
                    );
                    if (!visible(progressBar)) return false;

                    const containerText = progressBar.parentElement?.innerText || "";
                    let match = containerText.match(/(\\d+(?:[,.]\\d+)?)\\s*%/);
                    if (!match) {
                        const line = progressBar.querySelector(
                            ".kbq-progress-bar__line_determinate"
                        );
                        match = (line?.getAttribute("style") || "")
                            .match(/width\\s*:\\s*(\\d+(?:\\.\\d+)?)%/i);
                    }
                    if (!match) return false;

                    const progress = Number(match[1].replace(",", "."));
                    const normalize = (value) => value
                        .replace(/\\s+/g, " ")
                        .trim()
                        .toLocaleLowerCase();
                    const finishButton = Array.from(
                        document.querySelectorAll("button#finishBtn")
                    ).find((button) => (
                        normalize(button.innerText || "")
                        === normalize("Завершить тест-драйв")
                    ));
                    return progress > threshold
                        && visible(finishButton)
                        && !finishButton.disabled
                        && !finishButton.classList.contains("kbq-disabled");
                }
                """,
                arg=settings.FINISH_PROGRESS_THRESHOLD,
                timeout=settings.DOM_TIMEOUT_MS,
            )
        except Exception:
            pass
        progress, progress_label = await self.read_progress_percent()
        if progress <= settings.FINISH_PROGRESS_THRESHOLD:
            raise AssertionError(
                "После двух вводных подразделов прогресс должен быть "
                f"больше {settings.FINISH_PROGRESS_THRESHOLD}%, "
                f"получено {progress_label}%"
            )

        button = self._finish_button_locator("Завершить тест-драйв")
        await expect(button).to_be_visible()
        await expect(button).to_be_enabled()
        classes = (await button.get_attribute("class")) or ""
        if "kbq-disabled" in classes.split():
            raise AssertionError(
                "Кнопка «Завершить тест-драйв» содержит "
                "класс kbq-disabled при достаточном прогрессе"
            )
        log_step(f"[FINISH] Кнопка доступна при прогрессе {progress_label}%")
        return progress

    async def verify_finish_available_at_progress(
        self,
        minimum_progress: float,
    ) -> float:
        await self._ensure_finish_controls_visible("Завершить тест-драйв")
        progress, progress_label = await self.read_progress_percent()
        if progress < minimum_progress:
            raise AssertionError(
                "Прогресс ниже порога перехода в CTF: "
                f"ожидалось не менее {minimum_progress}%, "
                f"получено {progress_label}%"
            )
        await self._wait_for_finish_button_enabled("Завершить тест-драйв")
        log_step(
            "[FINISH] Кнопка завершения доступна перед CTF при "
            f"прогрессе {progress_label}%"
        )
        return progress

    async def submit_pro_request(
        self,
        *,
        company_name: str,
        company_tin: str,
        open_trigger: Locator | None = None,
    ) -> ProSubmissionResult:
        last_error: Exception | None = None
        statuses: list[int] = []
        last_dialog: Locator | None = None
        for attempt in range(
            1,
            settings.PRO_REQUEST_MAX_ATTEMPTS + 1,
        ):
            dialog = await self._open_and_fill_pro_dialog(
                company_name=company_name,
                company_tin=company_tin,
                open_trigger=open_trigger,
            )
            last_dialog = dialog
            submit = dialog.locator("button#proSaveBtn:visible").first
            observed: list[Response] = []
            response_received = asyncio.Event()

            def remember_pro_response(response: Response) -> None:
                if not observed and response_path_is(
                    response,
                    path="/api/request_pro_version/",
                    method="POST",
                ):
                    observed.append(response)
                    response_received.set()

            self.page.on("response", remember_pro_response)
            try:
                await self._click_pro_save_button(submit)
                response_wait = asyncio.create_task(response_received.wait())
                dom_wait = asyncio.create_task(
                    self._wait_for_pro_submission_dom(dialog)
                )
                try:
                    await asyncio.wait(
                        {response_wait, dom_wait},
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    response = observed[0] if response_received.is_set() else None
                    if response is not None:
                        statuses.append(response.status)
                    if response is not None and not response.ok:
                        message = await self._describe_pro_http_error(response)
                        if 400 <= response.status < 600:
                            raise ProRequestHttpError(message)
                        raise AssertionError(message)

                    await dom_wait
                finally:
                    for task in (response_wait, dom_wait):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(
                        response_wait,
                        dom_wait,
                        return_exceptions=True,
                    )

                closed = await self._close_pro_dialog_if_visible(dialog)
                response_label = (
                    f"HTTP {response.status}"
                    if response is not None
                    else "DOM без отдельного API-ответа"
                )
                log_step(
                    "[PRO] Заявка отправлена кнопкой #proSaveBtn "
                    "и подтверждена по DOM: "
                    f"{response_label}, попытка {attempt}"
                )
                return ProSubmissionResult(
                    attempts=attempt,
                    response_status=(response.status if response is not None else None),
                    dialog_closed=closed,
                )
            except Exception as exc:
                last_error = exc
                log_step(
                    f"[PRO] Попытка {attempt} не подтверждена: "
                    f"{compact_exception_message(exc)}"
                )
                await self._close_transient_overlay_above(dialog)
                await self._close_pro_dialog_if_visible(dialog)
                if isinstance(exc, ProRequestHttpError):
                    log_step(
                        "[PRO] Повторная отправка отключена после явного HTTP 4xx/5xx"
                    )
                    raise
            finally:
                self.page.remove_listener(
                    "response",
                    remember_pro_response,
                )

        closed = (
            True
            if last_dialog is None
            else await self._close_pro_dialog_if_visible(last_dialog)
        )
        status_text = (
            ", ".join(map(str, statuses)) if statuses else "ответ API не получен"
        )
        raise AssertionError(
            "Заявка на PRO-версию не отправлена за две попытки; "
            f"statuses={status_text}; dialog_closed={closed}"
        ) from last_error

    @staticmethod
    async def _describe_pro_http_error(response: Response) -> str:
        try:
            response_text = " ".join((await response.text()).split())
        except Exception:
            response_text = ""
        if len(response_text) > 500:
            response_text = f"{response_text[:497]}..."
        body_label = response_text or "тело ответа пустое"
        return f"PRO API вернул HTTP {response.status}; response={body_label}"

    async def submit_complex_scenario_pro_request(
        self,
        *,
        company_name: str,
        company_tin: str,
    ) -> ProSubmissionResult:
        iframe = self.page.frame_locator("iframe.scenarios-basic-result__iframe")
        link = (
            iframe.locator("a[onclick*=\"openDialogClick('pro')\"]")
            .filter(
                has_text=re.compile(
                    r"в PRO-версии",
                    re.IGNORECASE,
                )
            )
            .first
        )
        await expect(link).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        return await self.submit_pro_request(
            company_name=company_name,
            company_tin=company_tin,
            open_trigger=link,
        )

    async def _open_and_fill_pro_dialog(
        self,
        *,
        company_name: str,
        company_tin: str,
        open_trigger: Locator | None = None,
    ) -> Locator:
        dialog = self.page.locator("matrix-pro-version-dialog:visible").first
        if await dialog.count() == 0:
            open_button = (
                open_trigger
                if open_trigger is not None
                else self.page.locator(".trainer-pro-version-btn:visible").first
            )
            await safe_click(open_button)
            dialog = self.page.locator("matrix-pro-version-dialog:visible").first

        await expect(dialog).to_be_visible()
        await expect(
            dialog.get_by_text("Заявка на PRO-версию", exact=True)
        ).to_be_visible()

        company_field = dialog.locator("input#company_name").first
        tin_field = dialog.locator("input#company_tin").first
        await company_field.fill(company_name)
        await tin_field.fill(company_tin)
        await expect(company_field).to_have_value(company_name)
        await expect(tin_field).to_have_value(company_tin)

        consent = (
            dialog.locator("kbq-checkbox")
            .filter(
                has_text=re.compile(
                    r"согласие на\s+обработку моих персональных данных",
                    re.IGNORECASE,
                )
            )
            .first
        )
        consent_input = consent.locator("input[type='checkbox']").first
        await expect(consent_input).to_be_attached()
        if not await consent_input.is_checked():
            await safe_click(consent.locator("label").first)
        await expect(consent_input).to_be_checked()
        await expect(dialog.locator("button#proSaveBtn:visible").first).to_be_enabled()
        return dialog

    async def _click_pro_save_button(
        self,
        submit: Locator,
    ) -> None:
        await expect(submit).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        await submit.scroll_into_view_if_needed(timeout=settings.DOM_TIMEOUT_MS)
        await expect(submit).to_be_enabled(timeout=settings.DOM_TIMEOUT_MS)
        try:
            await submit.click(timeout=settings.DOM_TIMEOUT_MS)
        except Exception:
            await submit.click(force=True, timeout=settings.DOM_TIMEOUT_MS)
        log_step("[PRO] Нажата кнопка отправки заявки #proSaveBtn")

    async def finish_test_drive(self) -> None:
        await self._ensure_finish_controls_visible("Завершить тест-драйв")
        await self.verify_finish_available_after_introduction()
        await self._click_finish_button_enabled("Завершить тест-драйв")

        confirm = await self._visible_modal_button("Продолжить")

        _, response = await perform_and_wait_for_response(
            self.page,
            lambda: self._click_modal_button(confirm),
            predicate=lambda candidate: response_path_is(
                candidate,
                path="/api/basic/scenario/skip_basic_scenario/",
                method="POST",
            ),
            description="Завершение PT Sandbox test-drive",
        )
        self._test_drive_finish_submitted = True

        await self.page.wait_for_function(
            """
            () => {
                const visible = (element) => Boolean(
                    element
                    && getComputedStyle(element).visibility !== "hidden"
                    && getComputedStyle(element).display !== "none"
                    && element.getClientRects().length
                );
                const finish = document.querySelector("#finishBtn");
                const congratulations = document.querySelector(
                    "matrix-congratulation-basic-scenario, "
                    + "matrix-congratulation-basic-stage, "
                    + "matrix-congratulation"
                );
                const resultDialog = Array.from(
                    document.querySelectorAll(".cdk-overlay-pane")
                ).find((element) => (
                    visible(element)
                    && /поздрав|балл|точност|скорост|отзыв/i.test(
                        element.innerText || ""
                    )
                ));
                return !visible(finish)
                    || finish?.disabled
                    || visible(congratulations)
                    || visible(resultDialog);
            }
            """,
            timeout=settings.DOM_TIMEOUT_MS,
        )
        await self._click_finish_continue_if_visible()
        feedback_status = await self.submit_final_feedback(settings.FINAL_FEEDBACK_TEXT)
        log_step(
            "[FINISH] Test-drive завершен: "
            f"POST /api/basic/scenario/skip_basic_scenario/ "
            f"-> {response.status}, feedback -> "
            f"{feedback_status}"
        )

    async def ensure_test_drive_finished(self) -> None:
        """Физически завершает test-drive после раннего сбоя профиля."""

        if self._test_drive_finish_submitted:
            log_step("[FINISH][RECOVERY] Test-drive уже был завершен")
            return

        try:
            await self.return_from_ctf_to_basic_modules()
        except Exception as exc:
            log_step(
                "[FINISH][RECOVERY] Возврат из текущего экрана "
                "не подтвержден; заново открываем c-1: "
                f"{compact_exception_message(exc)}"
            )
            await self.open()

        await self.finish_test_drive()

    async def _ensure_finish_controls_visible(
        self,
        button_text: str,
    ) -> None:
        await self.page.wait_for_function(
            """
            ({ buttonText }) => {
                const visible = (element) => Boolean(
                    element
                    && getComputedStyle(element).visibility !== "hidden"
                    && getComputedStyle(element).display !== "none"
                    && element.getClientRects().length
                );
                const normalize = (value) => value
                    .replace(/\\s+/g, " ")
                    .trim()
                    .toLocaleLowerCase();
                const finishButton = Array.from(
                    document.querySelectorAll("button#finishBtn")
                ).find((button) => (
                    normalize(button.innerText || "")
                    === normalize(buttonText)
                ));
                const expand = Array.from(
                    document.querySelectorAll(
                        ".basic-scenario-actionbar-show"
                    )
                ).find((element) => (
                    visible(element)
                    && /Развернуть/i.test(element.innerText || "")
                ));
                return visible(finishButton) || Boolean(expand);
            }
            """,
            arg={"buttonText": button_text},
            timeout=settings.DOM_TIMEOUT_MS,
        )
        expanded = await self.page.evaluate(
            """
            (buttonText) => {
                const visible = (element) => Boolean(
                    element
                    && getComputedStyle(element).visibility !== "hidden"
                    && getComputedStyle(element).display !== "none"
                    && element.getClientRects().length
                );
                const normalize = (value) => value
                    .replace(/\\s+/g, " ")
                    .trim()
                    .toLocaleLowerCase();
                const finishButton = Array.from(
                    document.querySelectorAll("button#finishBtn")
                ).find((button) => (
                    normalize(button.innerText || "")
                    === normalize(buttonText)
                ));
                if (visible(finishButton)) return false;
                const expand = Array.from(
                    document.querySelectorAll(
                        ".basic-scenario-actionbar-show"
                    )
                ).find((element) => (
                    visible(element)
                    && /Развернуть/i.test(element.innerText || "")
                ));
                if (!expand) {
                    throw new Error(
                        "Neither finishBtn nor actionbar expand control is visible"
                    );
                }
                expand.click();
                return true;
            }
            """,
            button_text,
        )
        if expanded:
            log_step("[FINISH] Боковая панель модулей развернута DOM-кликом")
        await self.page.wait_for_function(
            """
            ({ buttonText }) => {
                const normalize = (value) => value
                    .replace(/\\s+/g, " ")
                    .trim()
                    .toLocaleLowerCase();
                const button = Array.from(
                    document.querySelectorAll("button#finishBtn")
                ).find((candidate) => (
                    normalize(candidate.innerText || "")
                    === normalize(buttonText)
                ));
                return Boolean(
                    button
                    && getComputedStyle(button).visibility !== "hidden"
                    && getComputedStyle(button).display !== "none"
                    && button.getClientRects().length
                );
            }
            """,
            arg={"buttonText": button_text},
            timeout=settings.DOM_TIMEOUT_MS,
        )

    async def open_final_feedback_from_finish_button(self) -> None:
        self._final_feedback_dialog_opened = False
        if await self._verify_auto_completed_without_final_dialog():
            self._test_drive_finish_submitted = True
            log_step(
                "[FINISH] Test-drive уже автоматически завершен на 100%: "
                "комплексный модуль completed, обе finish-кнопки disabled"
            )
            return

        await self._click_finish_button_enabled("Завершить сценарий")

        await self._wait_for_final_feedback_dialog_or_continue_button()
        await self._click_finish_continue_if_visible()

        dialog = self.page.locator("matrix-congratulation:visible").last
        await expect(dialog).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        self._final_feedback_dialog_opened = True
        self._test_drive_finish_submitted = True
        log_step("[FINISH] Final feedback dialog opened from finishBtn")

    async def _verify_auto_completed_without_final_dialog(self) -> bool:
        dialog = self.page.locator("matrix-congratulation:visible").last
        if await dialog.count() and await dialog.is_visible():
            return False

        progress, _ = await self.read_progress_percent()
        if progress < 100:
            return False

        complex_step = self.page.locator(
            f"#scenario_step_id_{settings.COMPLEX_STEP_ID}:visible"
        ).first
        if await complex_step.count() == 0:
            return False
        success_icon = complex_step.locator(
            "img[src*='success-filled.svg']:visible"
        ).first
        if await success_icon.count() == 0:
            return False

        visible_buttons = self.page.locator("button#finishBtn:visible")
        if await visible_buttons.count() == 0:
            return False
        for index in range(await visible_buttons.count()):
            button = visible_buttons.nth(index)
            classes = ((await button.get_attribute("class")) or "").split()
            if not await button.is_disabled() and "kbq-disabled" not in classes:
                return False
        return True

    async def scroll_complex_scenario_module_for_completion(self) -> None:
        iframe = self.page.locator("iframe.scenarios-basic-result__iframe").first
        await expect(iframe).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        await expect(iframe).to_have_attribute(
            "src",
            re.compile(r"/33_common_td_complex_ru\.html(?:[?#].*)?$"),
            timeout=settings.DOM_TIMEOUT_MS,
        )

        body = (
            self.page.frame_locator("iframe.scenarios-basic-result__iframe")
            .locator("body")
            .first
        )
        await expect(body).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

        box = await iframe.bounding_box()
        if box is not None:
            mouse_y_offset = max(
                5,
                min(box["height"] - 5, box["height"] / 2),
            )
            await self.page.mouse.move(
                box["x"] + (box["width"] / 2),
                box["y"] + mouse_y_offset,
            )

        for _ in range(10):
            await self.page.mouse.wheel(0, 500)
            await self.page.wait_for_timeout(100)

        await body.evaluate(
            """
            (element) => {
                const view = element.ownerDocument.defaultView;
                view.dispatchEvent(
                    new Event("scroll", { bubbles: true })
                );
            }
            """
        )
        await self.page.wait_for_timeout(settings.COMPLEX_SCENARIO_COMPLETION_DWELL_MS)
        log_step(
            "[INTRO][COMPLEX] Модуль «Комплексный сценарий» "
            "открыт, проскроллен вниз и выдержан для зачета"
        )

    async def submit_final_feedback(
        self,
        description: str,
    ) -> int | None:
        dialog = self.page.locator("matrix-congratulation:visible").last
        await expect(dialog).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        textarea = dialog.locator(
            "textarea[formcontrolname='description'], textarea.kbq-textarea, textarea"
        ).first
        await expect(textarea).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

        last_error: Exception | None = None
        statuses: list[int] = []
        for attempt in range(
            1,
            settings.FINAL_FEEDBACK_MAX_ATTEMPTS + 1,
        ):
            try:
                await expect(dialog).to_be_visible(timeout=5_000)
                await textarea.fill(description)
                await expect(textarea).to_have_value(description)
                submit = (
                    dialog.locator("button#congratulationModalSubmit:visible")
                    .filter(
                        has_text=re.compile(
                            r"^\s*Отправить\s*$",
                            re.IGNORECASE,
                        )
                    )
                    .first
                )
                _, response = await perform_and_wait_for_response(
                    self.page,
                    lambda: self._click_modal_button(submit),
                    predicate=lambda candidate: response_path_is(
                        candidate,
                        path="/api/common/scenario/submit_feedback/",
                        method="POST",
                    ),
                    description=(f"Отправка финального отзыва (попытка {attempt})"),
                    require_ok=False,
                )
                statuses.append(response.status)
                if not response.ok:
                    raise AssertionError(f"Feedback API вернул HTTP {response.status}")
                await dialog.wait_for(
                    state="hidden",
                    timeout=5_000,
                )
                log_step(
                    "[FEEDBACK] Финальный отзыв отправлен: "
                    f"HTTP {response.status}, попытка {attempt}"
                )
                return response.status
            except Exception as exc:
                last_error = exc
                log_step(
                    f"[FEEDBACK] Попытка {attempt} не подтверждена: "
                    f"{compact_exception_message(exc)}"
                )

        closed = await self._close_final_feedback_dialog(dialog)
        status_text = (
            ", ".join(map(str, statuses)) if statuses else "ответ API не получен"
        )
        if closed:
            log_step(
                "[FEEDBACK] Финальный отзыв не подтвержден; "
                "диалог закрыт крестиком без повторного завершения test-drive: "
                f"statuses={status_text}"
            )
            return None
        raise AssertionError(
            "Финальный отзыв не отправлен за две попытки; "
            f"statuses={status_text}; dialog_closed={closed}"
        ) from last_error

    async def submit_final_feedback_if_visible(
        self,
        description: str,
        *,
        timeout: int = 2_000,
    ) -> int | None:
        """Send final feedback only when the application rendered it.

        The CTF may finish without a final feedback dialog. That is a valid
        product flow: the caller must then return to the basic modules rather
        than waiting for a dialog that will never appear.
        """

        dialog = self.page.locator("matrix-congratulation:visible").last
        try:
            await expect(dialog).to_be_visible(timeout=timeout)
        except Exception:
            return None
        return await self.submit_final_feedback(description)

    async def _close_final_feedback_dialog(
        self,
        dialog: Locator,
    ) -> bool:
        if await dialog.count() == 0 or not await dialog.is_visible():
            return True

        candidates = (
            dialog.locator("button.kbq-modal-close:visible").first,
            dialog.locator(
                "i.close-btn[kbq-icon='kbq-xmark_16']:visible, "
                "i[kbq-icon='kbq-xmark_16']:visible"
            ).first,
        )
        for candidate in candidates:
            if await candidate.count() == 0:
                continue
            try:
                await candidate.click(force=True, timeout=5_000)
                await dialog.wait_for(
                    state="hidden",
                    timeout=5_000,
                )
                return True
            except Exception:
                continue
        return False

    async def verify_ctf_direct_transition(
        self,
        *,
        ctf_case_id: str,
        expect_prerequisites_dialog: bool | None = None,
    ) -> None:
        # The previous check may already have navigated to CTF. Do not return
        # to the introduction in that case: continue from the existing CTF
        # landing page and open its tasks in the CTF driver.
        if re.search(
            rf"[?&]scenario={re.escape(ctf_case_id)}(?:&|$)",
            self.page.url,
        ):
            if expect_prerequisites_dialog is True:
                raise AssertionError(
                    "Ожидалось окно required_for_ctf, но CTF открылся напрямую"
                )
            await self._wait_for_ctf_landing(ctf_case_id)
            return

        ctf_section = await self._visible_ctf_section()

        async def wait_for_ctf_dom() -> None:
            await self._wait_for_ctf_url_or_prerequisites_dialog(ctf_case_id)
            prerequisites_dialog = self.page.locator(
                "matrix-ctf-table-dialog:visible"
            ).first
            dialog_visible = await prerequisites_dialog.is_visible()
            if expect_prerequisites_dialog is False and dialog_visible:
                log_step(
                    "Все required_for_ctf-модули completed, "
                    "но окно подготовительных модулей появилось; "
                    "обрабатываем окно и продолжаем переход в CTF"
                )
            if expect_prerequisites_dialog is True and not dialog_visible:
                raise AssertionError(
                    "Required_for_ctf-модули не завершены, "
                    "но окно подготовительных модулей не появилось"
                )

            if dialog_visible:
                await self._continue_ctf_from_prerequisites_dialog(ctf_case_id)
            await self._wait_for_ctf_landing(ctf_case_id)

            if expect_prerequisites_dialog is False:
                log_step(
                    "[CTF] Окно required_for_ctf не показано: "
                    "подготовительные модули completed"
                )

        await perform_and_wait_for_dom_with_network_observation(
            self.page,
            lambda: safe_click(ctf_section),
            wait_for_ctf_dom,
            predicate=self._scenario_response,
            description=(f"Переход к CTF-сценарию {ctf_case_id} через модуль"),
        )
        log_step(f"[CTF] Модуль CTF выбран напрямую, открыт {ctf_case_id}")

    async def _visible_ctf_section(self) -> Locator:
        ctf_section = (
            self.page.locator(f"#id_{settings.CTF_SECTION_ID}:visible")
            .filter(
                has=self.page.get_by_text(
                    settings.CTF_SECTION_TITLE,
                    exact=True,
                )
            )
            .first
        )
        if not await ctf_section.is_visible():
            expand = (
                self.page.locator(".basic-scenario-actionbar-show:visible")
                .filter(has_text=re.compile(r"Развернуть", re.IGNORECASE))
                .first
            )
            if await expand.is_visible():
                await safe_click(expand)
        await expect(ctf_section).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        return ctf_section

    def _current_url_is_ctf(self, ctf_case_id: str) -> bool:
        return (
            re.search(
                rf"[?&]scenario={re.escape(ctf_case_id)}(?:&|$)",
                self.page.url,
            )
            is not None
        )

    def _ctf_tasks_button(self) -> Locator:
        return self.page.get_by_role(
            "button",
            name=re.compile(
                r"^\s*К заданиям\s*$",
                re.IGNORECASE,
            ),
        ).first

    async def _wait_for_ctf_landing(self, ctf_case_id: str) -> None:
        await self.page.wait_for_url(
            re.compile(rf"[?&]scenario={re.escape(ctf_case_id)}(?:&|$)"),
            timeout=settings.DOM_TIMEOUT_MS,
        )
        await self.page.wait_for_function(
            """
            () => {
                const visible = (element) => {
                    if (!element) return false;
                    const style = getComputedStyle(element);
                    return style.display !== "none"
                        && style.visibility !== "hidden"
                        && element.getClientRects().length > 0;
                };
                const controls = Array.from(
                    document.querySelectorAll(
                        "button, [kbqtablabelwrapper], .kbq-tab-label"
                    )
                ).some((element) => (
                    visible(element)
                    && /(К заданиям|Задания\\s+и\\s+результаты)/i.test(
                        element.innerText || ""
                    )
                ));
                const cards = Array.from(
                    document.querySelectorAll(
                        "[id^='matrix_scenarios_task_'], "
                        + ".scenario-tasks-card"
                    )
                ).some(visible);
                return controls || cards;
            }
            """,
            timeout=settings.DOM_TIMEOUT_MS,
        )

    async def _wait_for_ctf_url_or_prerequisites_dialog(
        self,
        ctf_case_id: str,
    ) -> None:
        await self.page.wait_for_function(
            """
            (caseId) => {
                const visible = (element) => {
                    if (!element) return false;
                    const style = getComputedStyle(element);
                    return style.display !== "none"
                        && style.visibility !== "hidden"
                        && element.getClientRects().length > 0;
                };
                const ctfUrl = new RegExp(
                    `[?&]scenario=${caseId}(?:&|$)`
                ).test(location.href);
                return ctfUrl || visible(
                    document.querySelector("matrix-ctf-table-dialog")
                );
            }
            """,
            arg=ctf_case_id,
            timeout=settings.DOM_TIMEOUT_MS,
        )

    async def _continue_ctf_from_prerequisites_dialog(
        self,
        ctf_case_id: str,
    ) -> None:
        dialog = self.page.locator("matrix-ctf-table-dialog:visible").first
        await expect(dialog).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

        for module_name in CTF_REQUIRED_MODULES:
            await expect(dialog.get_by_text(module_name, exact=True)).to_be_visible()

        await self._click_modal_button(self._ctf_prerequisites_continue_button(dialog))
        await self._wait_for_ctf_url_or_anyway_button(ctf_case_id)

        if not self._current_url_is_ctf(ctf_case_id):
            await self._click_modal_button(self._ctf_anyway_button())

        log_step("[CTF] Prerequisites-окно обработано, нажата кнопка перехода в CTF")

    def _ctf_prerequisites_continue_button(
        self,
        dialog: Locator,
    ) -> Locator:
        return (
            dialog.locator("button:visible")
            .filter(
                has_text=re.compile(
                    r"^\s*((Все|Всё)\s+равно\s+перейти|"
                    r"Приступить\s+к\s+CTF\s+сценарию)\s*$",
                    re.IGNORECASE,
                )
            )
            .last
        )

    def _ctf_anyway_button(self) -> Locator:
        return (
            self.page.locator(
                ".kbq-modal-content:visible button:visible, "
                ".cdk-overlay-pane:visible button:visible"
            )
            .filter(
                has_text=re.compile(
                    r"^\s*(Все|Всё)\s+равно\s+перейти\s*$",
                    re.IGNORECASE,
                )
            )
            .last
        )

    async def _wait_for_ctf_url_or_anyway_button(
        self,
        ctf_case_id: str,
    ) -> None:
        await self.page.wait_for_function(
            """
            (caseId) => {
                const visible = (element) => {
                    if (!element) return false;
                    const style = getComputedStyle(element);
                    return style.display !== "none"
                        && style.visibility !== "hidden"
                        && element.getClientRects().length > 0;
                };
                const ctfUrl = new RegExp(
                    `[?&]scenario=${caseId}(?:&|$)`
                ).test(location.href);
                const buttonVisible = Array.from(
                    document.querySelectorAll(
                        ".kbq-modal-content button, "
                        + ".cdk-overlay-pane button"
                    )
                ).some((button) => (
                    visible(button)
                    && /^(Все|Всё)\\s+равно\\s+перейти$/i.test(
                        (button.innerText || "").trim()
                    )
                ));
                return ctfUrl || buttonVisible;
            }
            """,
            arg=ctf_case_id,
            timeout=settings.DOM_TIMEOUT_MS,
        )

    async def return_from_ctf_to_basic_modules(self) -> None:
        basic_dom = self.page.locator(
            "[id^='scenario_step_id_']:visible, "
            "#scenario_step_id_161:visible, "
            "button#finishBtn:visible, "
            "iframe.scenarios-basic-result__iframe:visible, "
            ".scenarios-basic-result__tasks-container:visible"
        )

        if self._basic_scenario_url_is_open() or await basic_dom.count() > 0:
            log_step("[CTF] После финального окна уже открыты basic-модули")
            return

        try:
            await self.page.wait_for_function(
                """
                (basicCaseId) => {
                    const visible = (element) => {
                        if (!element) return false;
                        const style = getComputedStyle(element);
                        return style.display !== "none"
                            && style.visibility !== "hidden"
                            && element.getClientRects().length > 0;
                    };
                    const params = new URLSearchParams(location.search);
                    const pathname = location.pathname.endsWith("/")
                        ? location.pathname.slice(0, -1)
                        : location.pathname;
                    const basicUrl = pathname
                        === "/scenarios"
                        && params.get("scenario") === basicCaseId
                        && params.get("scenarioType") === "basic";
                    const basicReady = Array.from(
                        document.querySelectorAll(
                            "[id^='scenario_step_id_'], "
                            + "button#finishBtn, "
                            + "iframe.scenarios-basic-result__iframe, "
                            + ".scenarios-basic-result__tasks-container"
                        )
                    ).some(visible);
                    const returnReady = Array.from(
                        document.querySelectorAll(
                            "matrix-actionbar .actionbar__hide.cursor"
                        )
                    ).some((element) => (
                        visible(element)
                        && /Вернуться\\s+к\\s+модулям\\s+сценария/i.test(
                            element.innerText || ""
                        )
                    ));
                    const expandReady = Array.from(
                        document.querySelectorAll(
                            ".basic-scenario-actionbar-show"
                        )
                    ).some(visible);
                    return basicUrl || basicReady || returnReady || expandReady;
                }
                """,
                arg=settings.BASIC_CASE_ID,
                timeout=settings.DOM_TIMEOUT_MS,
            )
            if not self._basic_scenario_url_is_open() and await basic_dom.count() == 0:
                raise AssertionError("CTF требует явного возврата к basic-модулям")
            log_step(
                "[CTF] После финального окна выполнен автоматический возврат к модулям"
            )
            return
        except Exception:
            pass

        button = (
            self.page.locator("matrix-actionbar .actionbar__hide.cursor:visible")
            .filter(
                has_text=re.compile(
                    r"Вернуться\s+к\s+модулям\s+сценария",
                    re.IGNORECASE,
                )
            )
            .first
        )
        if not await button.is_visible():
            button = self.page.locator(
                "body > matrix-root > matrix-home > div > div > "
                "matrix-actionbar > div > div.full-width.flex-none > "
                "div.actionbar__hide.gap-4.cursor.ng-star-inserted"
            ).first
        if not await button.is_visible():
            button = self.page.get_by_text(
                re.compile(r"Вернуться\s+к\s+модулям", re.IGNORECASE)
            ).first
        if not await button.is_visible():
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
            if await expand.is_visible():
                await self._click_modal_button(expand)
                if self._basic_scenario_url_is_open() or await basic_dom.count() > 0:
                    log_step(
                        "[CTF] Basic-панель была свернута; "
                        "после раскрытия модули доступны"
                    )
                    return
                button = self.page.get_by_text(
                    re.compile(
                        r"Вернуться\s+к\s+модулям",
                        re.IGNORECASE,
                    )
                ).first
        if not await button.is_visible():
            if self._basic_scenario_url_is_open() or await basic_dom.count() > 0:
                log_step(
                    "[CTF] Basic-сценарий уже открыт; "
                    "прямой переход по URL не требуется"
                )
                return
            log_step(
                "[CTF] Элемент возврата отсутствует; "
                "открываем basic-сценарий c-1 напрямую"
            )
            await self.open()
            return
        await expect(button).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

        async def wait_for_basic_dom() -> None:
            await self.page.wait_for_function(
                """
                (basicCaseId) => {
                    const params = new URLSearchParams(location.search);
                    const pathname = location.pathname.endsWith("/")
                        ? location.pathname.slice(0, -1)
                        : location.pathname;
                    const basicUrl = pathname === "/scenarios"
                        && params.get("scenario") === basicCaseId
                        && params.get("scenarioType") === "basic";
                    const basicDom = Array.from(document.querySelectorAll(
                        "[id^='scenario_step_id_'], button#finishBtn, "
                        + "iframe.scenarios-basic-result__iframe, "
                        + ".scenarios-basic-result__tasks-container"
                    )).some((element) => {
                        const style = getComputedStyle(element);
                        return style.display !== "none"
                            && style.visibility !== "hidden"
                            && element.getClientRects().length > 0;
                    });
                    return basicUrl || basicDom;
                }
                """,
                arg=settings.BASIC_CASE_ID,
                timeout=settings.DOM_TIMEOUT_MS,
            )

        try:
            await perform_and_wait_for_dom_with_network_observation(
                self.page,
                lambda: safe_click(button),
                wait_for_basic_dom,
                predicate=self._scenario_response,
                description="Возврат из CTF к модулям test-drive",
            )
        except Exception as exc:
            if self._basic_scenario_url_is_open() or await basic_dom.count() > 0:
                log_step(
                    "[CTF] После DOM-перехода basic-сценарий уже открыт; "
                    "прямой переход по URL не требуется"
                )
                return
            log_step(
                "[CTF] DOM-возврат не подтвердился; "
                "открываем basic-сценарий c-1 напрямую: "
                f"{compact_exception_message(exc)}"
            )
            await self.open()
            return
        log_step("[CTF] Возврат к модулям выполнен по DOM")

    def _basic_scenario_url_is_open(self) -> bool:
        parsed = urlsplit(self.page.url)
        params = parse_qs(parsed.query)
        return (
            parsed.path.rstrip("/") == "/scenarios"
            and params.get("scenario") == [settings.BASIC_CASE_ID]
            and params.get("scenarioType") == ["basic"]
        )

    async def reopen_completed_ctf_from_modules(
        self,
        *,
        ctf_case_id: str,
    ) -> None:
        await self.return_from_ctf_to_basic_modules()

        ctf_section = await self._visible_ctf_section()

        async def wait_for_completed_ctf_dom() -> None:
            await self.page.wait_for_function(
                """
                (caseId) => {
                    const visible = (element) => {
                        if (!element) return false;
                        const style = getComputedStyle(element);
                        return style.display !== "none"
                            && style.visibility !== "hidden"
                            && element.getClientRects().length > 0;
                    };
                    const ctfUrl = new RegExp(
                        `[?&]scenario=${caseId}(?:&|$)`
                    ).test(location.href);
                    const tasksControl = Array.from(
                        document.querySelectorAll(
                            "button, [kbqtablabelwrapper], "
                            + ".kbq-tab-label"
                        )
                    ).some((element) => (
                        visible(element)
                        && /(К заданиям|Задания и результаты)/i.test(
                            element.innerText || ""
                        )
                    ));
                    return ctfUrl && tasksControl;
                }
                """,
                arg=ctf_case_id,
                timeout=settings.DOM_TIMEOUT_MS,
            )

        await perform_and_wait_for_dom_with_network_observation(
            self.page,
            lambda: safe_click(ctf_section),
            wait_for_completed_ctf_dom,
            predicate=self._scenario_response,
            description=("Повторное открытие завершенного CTF для итоговой статистики"),
        )
        log_step("[CTF][METRICS] Завершенный CTF повторно открыт через раздел id_5")

    async def _wait_for_pro_submission_dom(
        self,
        dialog: Locator,
    ) -> None:
        await self.page.wait_for_function(
            """
            () => {
                const visible = (element) => Boolean(
                    element
                    && getComputedStyle(element).visibility !== "hidden"
                    && getComputedStyle(element).display !== "none"
                    && element.getClientRects().length
                );
                const form = document.querySelector(
                    "matrix-pro-version-dialog form.pro-version-form"
                );
                const bodyText = document.body?.innerText || "";
                return !visible(form)
                    || /заявк[аи].*(отправлен|принят)|спасибо.*заявк/i
                        .test(bodyText);
            }
            """,
            timeout=settings.DOM_TIMEOUT_MS,
        )
        if await dialog.count() > 0 and await dialog.is_visible():
            form = dialog.locator("form.pro-version-form:visible")
            if await form.count() > 0:
                raise AssertionError(
                    "API принял PRO-заявку, но DOM формы "
                    "не перешел в итоговое состояние"
                )

    async def _close_transient_overlay_above(
        self,
        pro_dialog: Locator,
    ) -> None:
        overlays = self.page.locator(".cdk-overlay-pane:visible")
        if await overlays.count() <= 1:
            return

        top = overlays.last
        if await top.locator("matrix-pro-version-dialog").count():
            return
        close = top.locator("button.kbq-modal-close:visible").first
        if await close.count() > 0:
            await safe_click(close, timeout=5_000)
            await expect(top).to_be_hidden(timeout=5_000)
        await expect(pro_dialog).to_be_visible()

    async def _close_pro_dialog_if_visible(
        self,
        dialog: Locator,
    ) -> bool:
        if await dialog.count() == 0 or not await dialog.is_visible():
            return True
        close = dialog.locator("button.kbq-modal-close:visible").first
        if await close.count() == 0:
            return False
        await safe_click(close, timeout=5_000)
        await expect(dialog).to_be_hidden(timeout=5_000)
        return True

    async def submit_sos_report(self, description: str) -> SosSubmissionResult:
        log_step("[SCENARIO][SOS] Отправляем SOS")
        dialog = await self._open_sos_dialog()
        textarea = dialog.locator(
            "textarea[formcontrolname='description'], textarea.kbq-textarea, textarea"
        ).first
        await textarea.fill(description)
        await expect(textarea).to_have_value(description)

        baseline_close_count = await self.page.locator(
            "button.kbq-modal-close:visible"
        ).count()

        try:
            _, response = await perform_and_wait_for_response(
                self.page,
                self._click_sos_submit,
                predicate=lambda candidate: response_path_is(
                    candidate,
                    path="/api/common/scenario/submit_issue/",
                    method="POST",
                ),
                description="Отправка SOS",
                require_ok=False,
            )
        except Exception as exc:
            closed = await self._close_sos_dialogs()
            return SosSubmissionResult(
                request_ok=False,
                dialog_closed=closed,
                response_status=None,
                error=compact_exception_message(exc),
            )

        if not response.ok:
            closed = await self._close_sos_dialogs()
            return SosSubmissionResult(
                request_ok=False,
                dialog_closed=closed,
                response_status=response.status,
                error=f"HTTP {response.status}",
            )

        ticket_number = await self._read_sos_ticket_number(
            baseline_close_count=baseline_close_count
        )
        if ticket_number is None:
            closed = await self._close_sos_dialogs()
            return SosSubmissionResult(
                request_ok=False,
                dialog_closed=closed,
                response_status=response.status,
                error=(
                    "HTTP успешный, но номер обращения не появился в DOM результата SOS"
                ),
            )

        log_step(f"[SOS] Номер обращения: {ticket_number}")

        closed = await self._close_sos_dialogs()
        if closed:
            log_step("[SOS] Диалог результата/QR закрыт")
        return SosSubmissionResult(
            request_ok=True,
            dialog_closed=closed,
            response_status=response.status,
            ticket_number=ticket_number,
        )

    async def _click_finish_continue_if_visible(self) -> None:
        button = self._modal_button_locator("Продолжить").last
        if await button.count() == 0:
            return

        await self._click_modal_button(button)
        log_step("[FINISH] Кнопка «Продолжить» обработана")

    def _finish_button_locator(self, button_text: str) -> Locator:
        return (
            self.page.locator("button#finishBtn:visible")
            .filter(
                has_text=re.compile(
                    rf"^\s*{re.escape(button_text)}\s*$",
                    re.IGNORECASE,
                )
            )
            .first
        )

    async def _wait_for_finish_button_enabled(
        self,
        button_text: str,
    ) -> Locator:
        await self.page.wait_for_function(
            """
            ({ buttonText }) => {
                const visible = (element) => Boolean(
                    element
                    && getComputedStyle(element).visibility !== "hidden"
                    && getComputedStyle(element).display !== "none"
                    && element.getClientRects().length
                );
                const normalize = (value) => value
                    .replace(/\\s+/g, " ")
                    .trim()
                    .toLocaleLowerCase();
                const finishButton = Array.from(
                    document.querySelectorAll("button#finishBtn")
                ).find((button) => (
                    normalize(button.innerText || "")
                    === normalize(buttonText)
                ));
                return visible(finishButton)
                    && !finishButton.disabled
                    && !finishButton.classList.contains("kbq-disabled");
            }
            """,
            arg={"buttonText": button_text},
            timeout=settings.DOM_TIMEOUT_MS,
        )
        button = self._finish_button_locator(button_text)
        await expect(button).to_be_enabled(timeout=settings.DOM_TIMEOUT_MS)
        classes = (await button.get_attribute("class")) or ""
        if "kbq-disabled" in classes.split():
            raise AssertionError(
                "finishBtn is visible and enabled by DOM property, "
                "but still has kbq-disabled class"
            )
        return button

    async def _click_finish_button_enabled(
        self,
        button_text: str,
    ) -> None:
        await self._wait_for_finish_button_enabled(button_text)
        result = await self.page.evaluate(
            """
            (buttonText) => {
                const visible = (element) => Boolean(
                    element
                    && getComputedStyle(element).visibility !== "hidden"
                    && getComputedStyle(element).display !== "none"
                    && element.getClientRects().length
                );
                const normalize = (value) => value
                    .replace(/\\s+/g, " ")
                    .trim()
                    .toLocaleLowerCase();
                const finishButton = Array.from(
                    document.querySelectorAll("button#finishBtn")
                ).find((button) => (
                    normalize(button.innerText || "")
                    === normalize(buttonText)
                ));
                const finishDialogReady = Array.from(
                    document.querySelectorAll(
                        ".kbq-modal-content button, "
                        + ".cdk-overlay-pane button"
                    )
                ).some((button) => (
                    visible(button)
                    && /^\\s*Продолжить\\s*$/i.test(
                        button.innerText || ""
                    )
                ));
                if (
                    finishDialogReady
                    || visible(document.querySelector("matrix-congratulation"))
                ) {
                    return "dialog-already-open";
                }
                if (!visible(finishButton)) {
                    throw new Error("finishBtn is not visible");
                }
                if (
                    finishButton.disabled
                    || finishButton.classList.contains("kbq-disabled")
                ) {
                    throw new Error("finishBtn is disabled");
                }
                finishButton.click();
                return "clicked";
            }
            """,
            button_text,
        )
        log_step(f"[FINISH] Кнопка «{button_text}» обработана DOM-кликом: {result}")

    async def _wait_for_final_feedback_dialog_or_continue_button(
        self,
    ) -> None:
        await self.page.wait_for_function(
            """
            () => {
                const visible = (element) => Boolean(
                    element
                    && getComputedStyle(element).visibility !== "hidden"
                    && getComputedStyle(element).display !== "none"
                    && element.getClientRects().length
                );
                const finalDialog = document.querySelector(
                    "matrix-congratulation"
                );
                const continueButton = Array.from(
                    document.querySelectorAll(
                        ".kbq-modal-content button, "
                        + ".cdk-overlay-pane button"
                    )
                ).find((button) => (
                    visible(button)
                    && /^\\s*Продолжить\\s*$/i.test(
                        button.innerText || ""
                    )
                ));
                return visible(finalDialog) || visible(continueButton);
            }
            """,
            timeout=settings.DOM_TIMEOUT_MS,
        )

    def _modal_button_locator(self, text: str) -> Locator:
        return self.page.locator(
            ".kbq-modal-content:visible button:visible, "
            ".cdk-overlay-pane:visible button:visible"
        ).filter(has_text=re.compile(rf"^\s*{re.escape(text)}\s*$", re.IGNORECASE))

    async def _visible_modal_button(self, text: str) -> Locator:
        button = self._modal_button_locator(text).last
        await expect(button).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)
        return button

    async def _click_modal_button(self, button: Locator) -> None:
        try:
            await safe_click(button, timeout=5_000)
        except Exception:
            await button.click(force=True, timeout=5_000)

    async def _read_sos_ticket_number(
        self,
        *,
        baseline_close_count: int,
    ) -> str | None:
        del baseline_close_count
        try:
            ticket_handle = await self.page.wait_for_function(
                """
                () => {
                    const visible = (element) => {
                        if (!element) return false;
                        const style = getComputedStyle(element);
                        return style.display !== "none"
                            && style.visibility !== "hidden"
                            && element.getClientRects().length > 0;
                    };
                    const overlays = Array.from(
                        document.querySelectorAll(
                            ".kbq-modal-content, .cdk-overlay-pane, "
                            + "[role='dialog'], [class*='modal'], "
                            + "[class*='dialog']"
                        )
                    ).filter(visible);
                    const text = overlays
                        .map((element) => element.innerText || "")
                        .join("\\n");
                    const patterns = [
                        /(?:№|N[oº]?\\.?|#)\\s*([0-9][0-9\\s-]*)/iu,
                        /(?:номер\\s+обращения|обращени[ея])\\D{0,40}([0-9][0-9\\s-]*)/iu,
                    ];
                    for (const pattern of patterns) {
                        const match = text.match(pattern);
                        if (match) {
                            return match[1].replace(/\\D/g, "");
                        }
                    }
                    return false;
                }
                """,
                timeout=settings.DOM_TIMEOUT_MS,
            )
        except Exception:
            return None

        value = await ticket_handle.json_value()
        if value is None:
            return None
        ticket_number = str(value).strip()
        return ticket_number or None

    async def _open_sos_dialog(self) -> Locator:
        button = self.page.locator("button#sosBtn:visible").first
        await safe_click(button)
        textarea = self.page.locator(
            "textarea[formcontrolname='description']:visible"
        ).first
        await expect(textarea).to_be_visible()
        dialog = (
            self.page.locator(
                ".cdk-overlay-pane:visible, [role='dialog']:visible, "
                "[class*='modal']:visible, [class*='dialog']:visible"
            )
            .filter(has=textarea)
            .last
        )
        await expect(dialog).to_be_visible()
        log_step("[SOS] Окно отправки обращения открыто")
        return dialog

    async def _click_sos_submit(self) -> None:
        button = self.page.locator("button#reportModalSubmit:visible").last
        try:
            await safe_click(button, timeout=5_000)
        except Exception:
            await button.click(force=True, timeout=5_000)

    async def _close_sos_dialogs(self) -> bool:
        for _ in range(3):
            textarea = self.page.locator(
                "textarea[formcontrolname='description']:visible"
            )
            qr = self.page.locator(
                "[class*='qr']:visible, [class*='QR']:visible, "
                "img[src*='qr']:visible, img[src*='QR']:visible, "
                ".cdk-overlay-pane:visible canvas"
            )
            close_buttons = self.page.locator("button.kbq-modal-close:visible")

            if (
                await textarea.count() == 0
                and await qr.count() == 0
                and await close_buttons.count() == 0
            ):
                return True

            close_count_before = await close_buttons.count()
            if close_count_before == 0:
                await self.page.keyboard.press("Escape")
                continue

            close_button = close_buttons.last
            try:
                await close_button.click(force=True, timeout=2_000)
            except Exception:
                await self.page.keyboard.press("Escape")
                continue

            try:
                await expect(close_buttons).to_have_count(
                    max(0, close_count_before - 1),
                    timeout=2_000,
                )
            except Exception:
                continue

        return (
            await self.page.locator(
                "textarea[formcontrolname='description']:visible"
            ).count()
            == 0
            and await self.page.locator(
                "[class*='qr']:visible, [class*='QR']:visible, "
                "img[src*='qr']:visible, img[src*='QR']:visible, "
                ".cdk-overlay-pane:visible canvas"
            ).count()
            == 0
        )
