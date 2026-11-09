# PT Sandbox Test Drive

Автотест PT Matrix test-drive `sandbox/c-1` с CTF `sandbox/c-10`.
Реализация использует общие механизмы NAD/SIEM: пять профилей, проверку
вопросов по GitLab, статусы, прогресс, CTF-профили тренажера, Allure и
скриншоты ошибок.

## Контент

Перед каждым новым pytest-процессом загружаются файлы ветки `testdrive`:

- `MatrixInjector/Scenarios/sandbox/cases/c-1/doc/exercises.json`;
- `MatrixInjector/Scenarios/sandbox/cases/c-1/doc/sections.json`;
- `MatrixInjector/Scenarios/sandbox/cases/c-10/doc/exercises.json`.

Сначала используется GitLab API, а при его недоступности — временный shallow
clone через Git Credential Manager. Для Sandbox постоянный `content-cache`
отключен: JSON не записывается ни в репозиторий, ни во внешний файловый кэш.
Внутри одного pytest-процесса результат хранится только в памяти, чтобы пять
профилей работали с одним и тем же снимком контента. При следующем запуске
данные снова загружаются из GitLab.

## Профили

- `playwright_user_1 / perfect` проходит весь test-drive, проверяет SOS,
  вводные ссылки, видеотур, PRO, продуктовый интервал, все EX-модули, CTF,
  комплексный сценарий и финальный feedback.
- `playwright_user_2 / answers_and_hints` проходит basic-модули до первого
  прогресса `>= 70%`, затем CTF с неверными/правильными ответами и подсказками;
  после завершения test-drive проверяет `skipped_by_bs`.
- `playwright_user_3 / solutions` доходит до 70%, проверяет решения CTF и при
  отсутствии решения отправляет правильный ответ.
- `playwright_user_4 / stage_statuses` доходит до 70% и проверяет переходы
  статусов модулей/этапов.
- `playwright_user_5 / question_metrics` доходит до 70%, проверяет CTF-баллы,
  скорость, точность, итоговую статистику и лидерборд.

Профили 2–5 после достижения порога переходят прямо в раздел CTF, выполняют
свой профиль, затем обязательно завершают test-drive и отправляют feedback.

## Вводные ссылки

Каталог «О Тест-драйве» проверяется по реальному DOM. Ссылки должны вести на
первый модуль соответствующего раздела:

- «Веб-интерфейс продукта» → шаг 5 «Главное меню»;
- «Возможности обнаружения ВПО» → шаг 17 «Статический анализ»;
- «Сценарии использования» → шаг 21 «Анализ письма с вредоносным архивом»;
- «Комплексный сценарий» → шаг 161.

Подготовительные CTF-модули берутся из `sections.json`: шаг 8 «Страница
Объекты» и шаг 10 «Страница Источники».

## Проверка интервала PT Sandbox

В Matrix читается интервал текущего перехода, затем открывается продуктовая
ссылка и выполняется вход. В таблице продукта сценарий:

1. ожидает видимый заголовок AG Grid `col-id="time"`;
2. нажимает DOM-элемент `date-time-range-filter .filter-popover` с подписью
   «Дата создания»;
3. убеждается, что выбран `#radioPeriodRange` («Произвольный период»);
4. читает `startDate`, `startDateTime`, `endDate`, `endDateTime` из видимого
   popover;
5. сравнивает их с интервалом Matrix для этого перехода и закрывает вкладку.

Начальный интервал test-drive и интервал внутри CTF независимы и между собой
не сравниваются.

Контракт требует продуктовый host `192.168.91.14`. Логин и пароль можно
переопределить через `PT_TEST_DRIVE_SANDBOX_PRODUCT_LOGIN` и
`PT_TEST_DRIVE_SANDBOX_PRODUCT_PASSWORD`.

## Запуск

Из корня репозитория:

```powershell
.\.venv\Scripts\python.exe -m pytest -q playwright_test_drive\sandbox\tests
.\.venv\Scripts\python.exe -m pytest -v playwright_test_drive\sandbox\scenarios\sandbox_test_drive_scenario_test.py --headed
.\.venv\Scripts\python.exe -m pytest -v playwright_test_drive\sandbox\scenarios\sandbox_test_drive_scenario_test.py --headless --alluredir playwright_test_drive\sandbox\allure-results --clean-alluredir
```

Либо через локальный helper:

```powershell
.\scripts\check.ps1 -Scope unit -Product sandbox
.\scripts\check.ps1 -Scope live -Product sandbox -Headless -Allure
```

Нужные профили включаются полем `enabled` в
[`data/users.local.json`](data/users.local.json). Не запускайте live-сценарий
через `pytest-xdist`: параллельные браузеры уже управляются runner-ом.
