import sys
from pathlib import Path
from typing import Any

import pytest

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
for import_path in (
    TEST_DRIVE_ROOT / "common",
    TEST_DRIVE_ROOT,
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from pages.basic_modules_page import BasicModulesPage


class FakeMouse:
    def __init__(self, *, fail_on_move: int | None = None) -> None:
        self.events: list[tuple[Any, ...]] = []
        self._move_count = 0
        self._fail_on_move = fail_on_move

    async def move(
        self,
        x: float,
        y: float,
        *,
        steps: int = 1,
    ) -> None:
        self._move_count += 1
        self.events.append(("move", x, y, steps))
        if self._move_count == self._fail_on_move:
            raise RuntimeError("mouse move failed")

    async def down(self, *, button: str) -> None:
        self.events.append(("down", button))

    async def up(self, *, button: str) -> None:
        self.events.append(("up", button))


class FakePage:
    def __init__(self, *, fail_on_move: int | None = None) -> None:
        self.mouse = FakeMouse(fail_on_move=fail_on_move)
        self.waits: list[int] = []

    async def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)


class FakeItem:
    def __init__(self, box: dict[str, float]) -> None:
        self.box = box
        self.scrolled = False
        self.hovered = False
        self.selection_cleared = False

    async def scroll_into_view_if_needed(self) -> None:
        self.scrolled = True

    async def hover(self, *, timeout: int) -> None:
        self.hovered = True

    async def bounding_box(self) -> dict[str, float]:
        return self.box

    async def evaluate(self, expression: str) -> None:
        self.selection_cleared = "removeAllRanges" in expression


class FakeItems:
    def __init__(self, *items: FakeItem) -> None:
        self.items = items

    def nth(self, index: int) -> FakeItem:
        return self.items[index]


class StatefulDragDriver(BasicModulesPage):
    def __init__(self, answers: list[str]) -> None:
        super().__init__(FakePage())  # type: ignore[arg-type]
        self.answers = answers
        self.moves: list[tuple[int, int]] = []

    async def _find_drag_answer_list(self, _card, _desired_answers):
        return object()

    async def _verify_drag_prompts(self, _card, _exercise) -> None:
        return None

    def _drag_items(self, _answer_list):
        return object()

    async def _locator_texts(self, _locator) -> list[str]:
        return list(self.answers)

    async def _drag_item(
        self,
        _items,
        *,
        source_index: int,
        target_index: int,
        strategy: int,
    ) -> None:
        del strategy
        self.moves.append((source_index, target_index))
        answer = self.answers.pop(source_index)
        self.answers.insert(target_index, answer)

    async def _wait_for_drag_position(
        self,
        _answer_list,
        *,
        target_index: int,
        desired_answer: str,
    ) -> bool:
        return self.answers[target_index] == desired_answer

    async def _wait_for_drag_settled(self, _answer_list) -> None:
        return None


@pytest.mark.asyncio
async def test_drag_item_uses_incremental_mouse_gesture() -> None:
    page = FakePage()
    source = FakeItem({"x": 200.0, "y": 100.0, "width": 160.0, "height": 56.0})
    target = FakeItem({"x": 200.0, "y": 280.0, "width": 160.0, "height": 56.0})
    driver = BasicModulesPage(page)  # type: ignore[arg-type]

    await driver._drag_item(
        FakeItems(source, target),  # type: ignore[arg-type]
        source_index=0,
        target_index=1,
        strategy=1,
    )

    assert source.scrolled and target.scrolled
    assert source.hovered
    assert source.selection_cleared
    assert page.mouse.events == [
        ("move", 280.0, 128.0, 1),
        ("down", "left"),
        ("move", 280.0, 139.2, 4),
        ("move", 280.0, 324.8, 16),
        ("up", "left"),
    ]
    assert page.waits == [75, 50, 75]


@pytest.mark.asyncio
async def test_drag_item_releases_mouse_when_movement_fails() -> None:
    page = FakePage(fail_on_move=2)
    source = FakeItem({"x": 20.0, "y": 200.0, "width": 100.0, "height": 50.0})
    target = FakeItem({"x": 20.0, "y": 100.0, "width": 100.0, "height": 50.0})
    driver = BasicModulesPage(page)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="mouse move failed"):
        await driver._drag_item(
            FakeItems(source, target),  # type: ignore[arg-type]
            source_index=1,
            target_index=0,
            strategy=1,
        )

    assert page.mouse.events[-1] == ("up", "left")


@pytest.mark.asyncio
async def test_drag_order_preserves_equal_answers_already_in_place() -> None:
    driver = StatefulDragDriver(["10", "3", "3"])
    exercise = type(
        "Exercise",
        (),
        {
            "answer_mapping": (
                ("SMB", "3"),
                ("ngrok", "10"),
                ("exe", "3"),
            )
        },
    )()

    await driver._arrange_drag_answers(
        object(),  # type: ignore[arg-type]
        exercise,  # type: ignore[arg-type]
    )

    assert driver.answers == ["3", "10", "3"]
    assert driver.moves == [(1, 0)]
