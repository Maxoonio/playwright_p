# MaxPatrol SIEM Test Drive

Автотест test-drive `siem/c-2` на PT Matrix `192.168.91.61` с CTF
`siem/c-20`. Реализация повторяет профили и проверки NAD, но использует
собственные номера модулей, ссылки, CTF prerequisites и переход в
MaxPatrol SIEM.

## Контент

Перед каждым pytest-процессом SIEM по умолчанию заново загружает из ветки
`testdrive`:

- `MatrixInjector/Scenarios/siem/cases/c-2/doc/exercises.json`;
- `MatrixInjector/Scenarios/siem/cases/c-2/doc/sections.json`;
- `MatrixInjector/Scenarios/siem/cases/c-20/doc/exercises.json`.

Сначала используется GitLab API, затем временный shallow clone через Git
Credential Manager. Постоянный кэш не содержит токен и используется только
как fallback, если GitLab недоступен. Чтобы явно разрешить запуск из кэша без
попытки обновления, задайте `PT_TEST_DRIVE_REFRESH_CONTENT=false`.

Перед открытием браузеров проверяется контракт свежего контента: `c-2/c-20`,
вводные модули `1–4`, ссылки «Сценарии использования», «Поиск флага (CTF)»,
«Комплексный сценарий» и «Приложения» с точными DOM-переходами на шаги
`5`, CTF `c-20`, `161` и `17`, а также подготовительные модули CTF
`8`, `10`, `14`.

## Пять профилей

- `perfect` — SOS, ссылки, видеотур, PRO, статусы и прогресс всех модулей,
  вопросы из GitLab, переход и интервал MaxPatrol SIEM, полный CTF, feedback
  и итоговые 100%;
- `answers_and_hints` — неправильные ответы, подсказки и штрафы CTF;
- `solutions` — решения и нулевые баллы;
- `stage_statuses` — успешный, частичный и нулевой статусы этапов;
- `question_metrics` — points, accuracy, speed и итоговая статистика.

Для `question_metrics` точность проверяется по двум разным формулам, как в
NAD: профиль использует `S / completed`, а лидерборд —
`S / (completed + skipped)`. Поэтому разные проценты на этих двух экранах
допустимы только тогда, когда они получаются из одной суммы точности `S`.
Баллы и скорость в профиле, лидерборде и dashboard должны совпадать.

Профили 2–5 после каждого basic-модуля считывают общий прогресс из DOM,
продолжают до первого значения `>= 70%`, проверяют предупреждение кнопки
«Следующий модуль», выполняют свой CTF-профиль, завершают test-drive общей
кнопкой и сверяют лидерборд. Учетные данные находятся в
[`data/users.local.json`](data/users.local.json) и совпадают с NAD.

## Покрытие чек-листа

Сценарий проверяет статусы `undefined`, `started`, `partial_success`,
`completed`, `skipped_by_bs`, статус `failed` у вопроса, расчет прогресса и
порог кнопки завершения. Также проверяются ссылки вводного модуля, видеотур,
тексты/варианты/ответы/подсказки из свежего JSON, «К заданиям», «Следующий
модуль», копирование фильтров, `required_for_ctf`, PRO-окно комплексного
сценария, оба feedback-flow и SOS с номером заявки.

Профиль `stage_statuses` в каждом EX-модуле намеренно создает цепочку
`undefined -> started -> partial_success -> completed`: после первого
правильного ответа следующий вопрос временно оставляется в `failed`, статус
модуля проверяется, затем вопрос исправляется. После завершения test-drive
непройденные модули этого профиля отдельно проверяются как `skipped_by_bs`.

Модуль «Инфраструктура стенда» обязательно прокручивается до точного низа
iframe DOM. Продуктовые логин и скрытый пароль читаются из «Инструкций»:
пароль получается только нажатием видимой DOM-кнопки копирования, после чего
проверяется переход и временной интервал MaxPatrol SIEM.

## Запуск

Для строгой проверки начальных `undefined`-статусов прогресс пользователей
должен быть очищен. Повторный прогон на накопленном состоянии разрешен, но
уже пройденные/частично пройденные CTF и недоступные учетные записи продукта
будут честно отражены как внешние расхождения стенда.

```powershell
.\.venv\Scripts\python.exe -m pytest -q playwright_test_drive\siem\tests
.\.venv\Scripts\python.exe -m pytest -v playwright_test_drive\siem\scenarios\siem_test_drive_scenario_test.py --headless
```

Allure:

```powershell
.\.venv\Scripts\python.exe -m pytest -v playwright_test_drive\siem\scenarios\siem_test_drive_scenario_test.py --headless --alluredir playwright_test_drive\siem\allure-results --clean-alluredir
```

SIEM через абсолютный путь:

```powershell
C:\Users\mbaranov\PycharmProjects\playwright_pt\.venv\Scripts\python.exe -m pytest -v C:\Users\mbaranov\PycharmProjects\playwright_pt\playwright_test_drive\siem\scenarios\siem_test_drive_scenario_test.py --headless --alluredir C:\Users\mbaranov\PycharmProjects\playwright_pt\playwright_test_drive\siem\allure-results --clean-alluredir
C:\Users\mbaranov\PycharmProjects\playwright_pt\.venv\Scripts\python.exe -m pytest -v C:\Users\mbaranov\PycharmProjects\playwright_pt\playwright_test_drive\siem\scenarios\siem_test_drive_scenario_test.py --headed --alluredir C:\Users\mbaranov\PycharmProjects\playwright_pt\playwright_test_drive\siem\allure-results --clean-alluredir
```

SIEM сохраняет Allure только в `siem/allure-results`, а error/failed-
скриншоты — только в `siem/artifacts/screenshots`; каталоги NAD при этом не
используются.
