# Playwright Test Drive

Независимый проект автотестов PT Matrix Test Drive на `192.168.91.61`.
Test-drive использует общие CTF-профили из `playwright_trainer`, чтобы
разбаловка, статусы и статистика проверялись одной реализацией.

## Структура

- [`common`](common) — общие модели, загрузка GitLab-контента, page objects,
  CTF-bridge, отчетность, браузерные и DOM-утилиты;
- [`nad`](nad) — существующий NAD test-drive `c-3` с CTF `c-1`;
- [`siem`](siem) — MaxPatrol SIEM test-drive `c-2` с CTF `c-20`;
- [`sandbox`](sandbox) — PT Sandbox test-drive `c-1` с CTF `c-10`.

У каждого продукта свои настройки, пять пользователей, продуктовый page
object, сценарий, unit-тесты и runtime-артефакты. Общая реализация находится
только в `common`; уникальные DOM-контракты остаются в `nad` и `siem`.
Запускать продукты нужно отдельными командами, чтобы их пользовательские
состояния и отчеты не смешивались.

## Профили пользователей

В [`nad/data/users.local.json`](nad/data/users.local.json) и
[`siem/data/users.local.json`](siem/data/users.local.json) и
[`sandbox/data/users.local.json`](sandbox/data/users.local.json) настроены
одинаковые пять пользовательских профилей:

- `playwright_user_1`, `perfect` — проверяет SOS, PRO, ссылки и видео
  первых двух модулей, кнопки и статусы всех EX-модулей, интервал продукта,
  проходит весь test-drive и CTF на 100%;
- `playwright_user_2`, `answers_and_hints` — доходит до 70% test-drive,
  затем проверяет неправильные ответы, подсказки, их тексты и штрафы;
- `playwright_user_3`, `solutions` — доходит до 70%, открывает решения,
  сверяет номера заданий и статусы 0 баллов;
- `playwright_user_4`, `stage_statuses` — доходит до 70% и проверяет
  успешный, частичный и нулевой статусы этапов CTF;
- `playwright_user_5`, `question_metrics` — доходит до 70% и проверяет
  разбаловку, скорость, точность, общую и профильную статистику по циклу
  тренажера. Общие метрики читаются после каждого обычного вопроса,
  после обработки окна последнего вопроса этапа и из финального окна.

Пользователи 2–5 также проверяют ссылки и видео первых двух модулей, но
не выполняют SOS/PRO, проверку интервала и служебные проверки кнопок
каждого EX-модуля. После CTF первый профиль возвращается в комплексный
сценарий. Пользователи 2–5 не открывают последний комплексный модуль
(`36` у NAD, `161` у SIEM) и завершают test-drive общей кнопкой. Профиль
`question_metrics` использует метрики, полученные
во время прохождения и в финальном окне, и не открывает завершенный CTF
повторно. После завершения только профиль `answers_and_hints` проверяет
`skipped_by_bs` у оставшегося EX-модуля и его первой карточки. Затем
каждый специализированный профиль сверяет строку пользователя в рейтинге
со статистикой профиля.

## Проверка статусов и прогресса

Основной профиль проверяет точные переходы, а не только наличие любой
известной иконки:

- `undefined` — модуль еще не начинали;
- `failed` — карточка вопроса после заведомо неправильного ответа;
- `started` — в модуле с несколькими вопросами принят первый ответ;
- `partial_success` — часть вопросов завершена, часть осталась
  нерешенной или ошибочной;
- `completed` — вопрос и затем весь модуль закрыты корректно;
- `skipped_by_bs` — модуль и вопрос пропущены после досрочного
  завершения test-drive.

После каждого обработанного basic-модуля каждый сценарный профиль считывает
общий процент из DOM. Полный профиль дополнительно сверяет его с количеством
модулей в статусе `completed`, а профили 2–5 продолжают basic до первого
фактического значения `>= 70%` и только затем переходят в CTF. Статусы
`started`, `partial_success`, `undefined` и `skipped_by_bs` в процент
завершения не засчитываются.

Теоретический модуль «Инфраструктура стенда» открывается по точной карточке,
а его iframe прокручивается реальными DOM/mouse-событиями до подтвержденного
нижнего края. Если стенд после этого не меняет статус из-за отсутствующего
backend `exercise_uuid`, проверка прокрутки сохраняется, а внешний дефект
отмечается отдельно и не подменяется прямым API-вызовом.

Необратимая ветка раннего завершения вынесена в поддерживаемый профиль
`introduction_finish`: он завершает test-drive после одного вопроса
модуля 3 и проверяет `completed` у отвеченного вопроса,
`skipped_by_bs` у нерешенных вопросов, `partial_success` у самого
модуля и `skipped_by_bs` у непройденных модулей. Для этого профиля
нужна отдельная чистая учетная запись.

## Источник сценария

Локальных `scenario.json`, `exercises.json` и `sections.json` в проекте нет.
NAD читает базовую подготовку из ветки `dev_trainer`, SIEM — из `testdrive`:

- `MatrixInjector/Scenarios/nad/cases/c-3/doc/exercises.json`;
- `MatrixInjector/Scenarios/nad/cases/c-3/doc/sections.json`;
- `MatrixInjector/Scenarios/siem/cases/c-2/doc/exercises.json`;
- `MatrixInjector/Scenarios/siem/cases/c-2/doc/sections.json`;
- `MatrixInjector/Scenarios/siem/cases/c-20/doc/exercises.json`.

Сначала используется GitLab API. Для закрытого проекта можно задать токен:

```powershell
$env:PT_TRAINER_CONTENT_TOKEN = "<gitlab-token>"
```

Если токена нет, загрузчик выполняет временный shallow clone через
настроенный Git Credential Manager. Временный каталог удаляется после
чтения. После первой успешной загрузки исходные JSON сохраняются без
токена во внешний локальный кэш:
`%LOCALAPPDATA%\PTMatrixPlaywright\test-drive\content-cache`.
NAD может повторно использовать кэш без токена. SIEM по умолчанию перед
каждым pytest-процессом заново запрашивает оба JSON из ветки `testdrive`;
кэш остается только аварийным fallback при недоступности GitLab. Внутри
одного запуска модели загружаются один раз и передаются всем пяти
пользователям.

Ветка NAD задаётся `CONTENT_GITLAB_REF` в `nad/common/settings.py`
(переопределение — `PT_TRAINER_CONTENT_REF`). Кэш другой ветки не используется.
Поддерживаются оба формата секций: `steps/sections` и `kind/children`.

Путь кэша можно переопределить:

```powershell
$env:PT_TEST_DRIVE_CONTENT_CACHE_DIR = "C:\temp\pt-test-drive-content-cache"
```

Чтобы принудительно обновить кэш из GitLab:

```powershell
$env:PT_TEST_DRIVE_REFRESH_CONTENT = "true"
```

При необходимости источник переопределяется переменными:

```powershell
$env:PT_TRAINER_CONTENT_GITLAB_URL = "https://gitlab.ptsecurity.com"
$env:PT_TRAINER_CONTENT_PROJECT = "sbd/pt-trainer-content"
$env:PT_TRAINER_CONTENT_REF = "testdrive"
```

## Что проверяет полный профиль

- статусы разделов и модулей до и после выполнения;
- раскрытие и сворачивание боковой панели в каждом EX-модуле;
- переход кнопкой «К заданиям»;
- первый вход в продукт, браузерные предупреждения и авторизация;
- повторный переход в продукт и совпадение временного интервала с Matrix;
- совпадение текста, вариантов и правильных ответов с GitLab;
- вопросы `input`, `radio`, `checkbox` и `drag-n-drop`;
- открытие и совпадение подсказок с GitLab для вопросов, где они заданы;
- API `/api/basic/exercise/submit_basic/` и DOM-статус `completed`;
- поздравительные окна после `required_for_ctf`-модулей;
- отсутствие окна prerequisites и прямой переход в CTF после их прохождения;
- DOM-раскрытие боковой панели каждого CTF-этапа, схемы и видео;
- баллы, точность и скорость CTF-вопросов и всего сценария;
- финальное поздравительное окно CTF, отправку отзыва и возврат к модулям.

PRO-заявка проверяется через `/api/request_pro_version/` и итоговый DOM.
При ошибке выполняются две попытки, после второй оставшееся окно закрывается.

Сетевые ожидания ставятся до действий, а результат подтверждается по DOM.
Для чистых SPA-переходов отсутствие отдельного запроса не создает
искусственного тайм-аута. Скриншоты сохраняются только для `FAILED` и
`ERROR`; имя файла содержит статус, пользователя, раздел, шаг и проверку.

## Запуск

Установка зависимостей test-drive:

```powershell
.\.venv\Scripts\python.exe -m pip install -r playwright_test_drive\requirements.txt
```

NAD из корня общего репозитория:

```powershell
.\.venv\Scripts\python.exe -m pytest -v playwright_test_drive\nad\scenarios\test_drive_scenario_test.py
```

SIEM из корня общего репозитория:

```powershell
.\.venv\Scripts\python.exe -m pytest -v playwright_test_drive\siem\scenarios\siem_test_drive_scenario_test.py
```

Только быстрые локальные тесты без прохождения стенда:

```powershell
.\.venv\Scripts\python.exe -m pytest -q playwright_test_drive\nad\tests
.\.venv\Scripts\python.exe -m pytest -q playwright_test_drive\siem\tests
.\.venv\Scripts\python.exe -m pytest -q playwright_test_drive\sandbox\tests
```

По умолчанию используется видимый системный Chrome. Явный запуск с
интерфейсом:

```powershell
.\.venv\Scripts\python.exe -m pytest -v playwright_test_drive\nad\scenarios\test_drive_scenario_test.py --headed
```

Headless-запуск:

```powershell
.\.venv\Scripts\python.exe -m pytest -v playwright_test_drive\nad\scenarios\test_drive_scenario_test.py --headless
.\.venv\Scripts\python.exe -m pytest -v playwright_test_drive\siem\scenarios\siem_test_drive_scenario_test.py --headless
.\.venv\Scripts\python.exe -m pytest -v playwright_test_drive\sandbox\scenarios\sandbox_test_drive_scenario_test.py --headless
```

Запуск из любой директории через абсолютный путь:

```powershell
C:\Users\mbaranov\PycharmProjects\playwright_pt\.venv\Scripts\python.exe -m pytest -v C:\Users\mbaranov\PycharmProjects\playwright_pt\playwright_test_drive\nad\scenarios\test_drive_scenario_test.py --headless --alluredir C:\Users\mbaranov\PycharmProjects\playwright_pt\playwright_test_drive\nad\allure-results --clean-alluredir
C:\Users\mbaranov\PycharmProjects\playwright_pt\.venv\Scripts\python.exe -m pytest -v C:\Users\mbaranov\PycharmProjects\playwright_pt\playwright_test_drive\siem\scenarios\siem_test_drive_scenario_test.py --headless --alluredir C:\Users\mbaranov\PycharmProjects\playwright_pt\playwright_test_drive\siem\allure-results --clean-alluredir
```

Запуск с окном браузера через абсолютный путь:

```powershell
C:\Users\mbaranov\PycharmProjects\playwright_pt\.venv\Scripts\python.exe -m pytest -v C:\Users\mbaranov\PycharmProjects\playwright_pt\playwright_test_drive\nad\scenarios\test_drive_scenario_test.py --headed --alluredir C:\Users\mbaranov\PycharmProjects\playwright_pt\playwright_test_drive\nad\allure-results --clean-alluredir
C:\Users\mbaranov\PycharmProjects\playwright_pt\.venv\Scripts\python.exe -m pytest -v C:\Users\mbaranov\PycharmProjects\playwright_pt\playwright_test_drive\siem\scenarios\siem_test_drive_scenario_test.py --headed --alluredir C:\Users\mbaranov\PycharmProjects\playwright_pt\playwright_test_drive\siem\allure-results --clean-alluredir
```

Совместимый вариант через окружение:

```powershell
$env:PT_TEST_DRIVE_HEADLESS = "true"
.\.venv\Scripts\python.exe -m pytest -v playwright_test_drive\nad\scenarios\test_drive_scenario_test.py
```

Все включенные пользователи запускаются в отдельных incognito-браузерах.
Лимит параллельности задается константой `MAX_PARALLEL_BROWSERS` в
`nad/common/settings.py` или `siem/common/settings.py`. Значение `0` означает запуск всех
включенных пользователей параллельно.
Состав запуска задается полем `enabled` в
`nad/data/users.local.json`. Для одного профиля оставьте
`enabled: true` только у нужной учетной записи.

## Allure

Python-адаптер входит в `requirements.txt`. Запуск с чистым набором
Allure-результатов:

```powershell
.\.venv\Scripts\python.exe -m pytest -v playwright_test_drive\nad\scenarios\test_drive_scenario_test.py --headless --alluredir playwright_test_drive\nad\allure-results --clean-alluredir
```

Для SIEM замените `nad\scenarios\test_drive_scenario_test.py` на
`siem\scenarios\siem_test_drive_scenario_test.py`, а каталог результатов —
на `playwright_test_drive\siem\allure-results`.

В отчет попадают пользователи, профили, разделы, модули, все внутренние
проверки, итоговая текстовая и JSON-сводки, окружение запуска и
скриншоты для `FAILED`/`ERROR`.

Артефакты продуктов физически разделены: NAD пишет в
`nad/allure-results` и `nad/artifacts/screenshots`, SIEM — в
`siem/allure-results` и `siem/artifacts/screenshots`.

Для просмотра нужен Allure CLI:

```powershell
npm install -g allure-commandline
allure serve playwright_test_drive\nad\allure-results
```

Либо можно собрать статический отчет:

```powershell
allure generate playwright_test_drive\nad\allure-results -o playwright_test_drive\nad\allure-report --clean
allure open playwright_test_drive\nad\allure-report
```

Основные параметры стенда, SOS/PRO-тексты, таймауты и лимит
параллельности лежат константами в `nad/common/settings.py`.
Через окружение передаются только секреты, GitLab-настройки контента,
путь к content-cache и headless-режим для совместимости с запуском без
CLI-флага.
