from utils.logging import bind_log_context, log_step, reset_log_context


def test_test_drive_log_contains_user_and_profile(capsys) -> None:
    token = bind_log_context(
        username="playwright_user_3",
        profile="solutions",
    )
    try:
        log_step("[BROWSER] Запущен отдельный браузер")
    finally:
        reset_log_context(token)

    output = capsys.readouterr().out
    assert (
        "playwright_user_3 | solutions | [BROWSER] Запущен отдельный браузер" in output
    )


def test_test_drive_result_line_is_not_prefixed_twice(capsys) -> None:
    token = bind_log_context(
        username="playwright_user_3",
        profile="solutions",
    )
    try:
        log_step("PASSED | playwright_user_3 | solutions | SECTION 5 | STEP 2")
    finally:
        reset_log_context(token)

    output = capsys.readouterr().out
    assert output.count("playwright_user_3 | solutions") == 1
