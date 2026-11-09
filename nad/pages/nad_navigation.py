from __future__ import annotations

from dataclasses import replace

import settings
from models import BasicScenarioContent
from playwright.async_api import Page, expect
from utils.logging import log_step
from utils.ui import safe_click


async def expand_navigation_section(header) -> None:
    # The icon has pointer-events disabled and Angular replaces it on expand.
    # Dispatch to its clickable wrapper only while it is still collapsed.
    await header.evaluate(
        """header => {
            const collapsed = header.querySelector(
                "i[kbq-icon='kbq-chevron-down-s_16'], i.kbq-chevron-down-s_16"
            );
            if (collapsed) collapsed.parentElement.click();
        }"""
    )
    await expect(
        header.locator("i[kbq-icon='kbq-chevron-up-s_16'], i.kbq-chevron-up-s_16").first
    ).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)


async def read_navigation_layout(page: Page) -> dict[int, dict]:
    """Read the visible language panel after materializing collapsed sections."""
    headers = page.locator(".actionbar__section-container:visible")
    expand_panel = page.locator(".basic-scenario-actionbar-show:visible").first
    # The expand control can appear before Angular has mounted the sidebar.
    # Wait for the section DOM first; clicking that transient control races
    # with rendering and can wait on an element which has already disappeared.
    await expect(page.locator(".actionbar__section-container").first).to_be_attached(
        timeout=settings.APP_START_TIMEOUT_MS
    )
    if not await headers.count():
        await safe_click(expand_panel)
    await expect(headers.first).to_be_visible(timeout=settings.DOM_TIMEOUT_MS)

    # Angular removes collapsed child sections and modules from the DOM.
    # Expand parents first, then repeat to discover newly mounted children.
    collapsed = headers.filter(
        has=page.locator("i[kbq-icon='kbq-chevron-down-s_16'], i.kbq-chevron-down-s_16")
    )
    for _ in range(64):
        if not await collapsed.count():
            break
        section_id = await collapsed.first.get_attribute("id")
        header = page.locator(f"#{section_id}:visible").first
        log_step(f"[STRUCTURE] Раскрытие раздела {section_id}")
        await expand_navigation_section(header)
    else:
        raise AssertionError("NAD: структура содержит более 64 свернутых разделов")

    rows = await page.locator("[id^='scenario_step_id_']:visible").evaluate_all(
        r"""
        (nodes) => nodes.map(node => {
            const sections = [];
            for (let parent = node.parentElement; parent; parent = parent.parentElement) {
                const header = Array.from(parent.children).find(child => (
                    child.classList.contains('actionbar__section-container')
                ));
                if (header) {
                    sections.unshift([
                        Number(header.id.replace('id_', '')),
                        header.querySelector('.actionbar__step-title').textContent.trim()
                    ]);
                }
                if (parent.classList.contains('actionbar__steps-container')) break;
            }
            return {
                step_id: Number(node.id.replace('scenario_step_id_', '')),
                sections
            };
        })
        """
    )
    layout: dict[int, dict] = {}
    for row in rows:
        step_id = row["step_id"]
        if step_id in layout:
            raise AssertionError(
                f"NAD: несколько видимых модулей scenario_step_id_{step_id}"
            )
        layout[step_id] = row
    intro = layout.get(1, {}).get("sections", [])
    if not intro:
        raise AssertionError(
            "NAD: вводный модуль scenario_step_id_1 не найден в разделах"
        )
    log_step(
        f"[STRUCTURE] NAD: модулей={len(layout)}, "
        f"вводный раздел=id_{intro[0][0]} «{intro[0][1]}»"
    )
    return layout


def bind_content_to_layout(
    content: BasicScenarioContent,
    layout: dict[int, dict],
    *,
    allow_missing: bool = False,
) -> BasicScenarioContent:
    expected = {module.step_id for module in content.modules}
    actual = set(layout)
    if expected != actual and not allow_missing:
        raise AssertionError(
            "NAD CONTENT_DOM_MISMATCH: "
            f"нет модулей в DOM={sorted(expected - actual)}, "
            f"нет модулей в контенте={sorted(actual - expected)}"
        )
    # Keep GitLab answers/documents intact and leave the cached object immutable.
    return replace(
        content,
        modules=tuple(
            replace(
                module,
                ancestor_section_ids=tuple(
                    section_id for section_id, _ in layout[module.step_id]["sections"]
                ),
                ancestor_section_titles=tuple(
                    title for _, title in layout[module.step_id]["sections"]
                ),
            )
            for module in content.modules
            if module.step_id in layout
        ),
    )
