"""Отправляет менеджерам их задачи на сегодня из Google-таблиц в MAX.

Задачей на сегодня считается строка, у которой дедлайн приходится на
сегодняшнее число (состояние «Сегодня»).

Настройки задаются в файле config.json в текущем рабочем каталоге:

{
    "max_bot_token": "токен бота MAX",
    "tables": [
        {
            "url": "https://docs.google.com/spreadsheets/d/<id>/edit?gid=<gid>",
            "chat_ids": ["213842560", "987654321"]
        }
    ]
}

Токен можно также задать переменной окружения MAX_BOT_TOKEN
(переменная имеет приоритет над config.json).

Скрипт рассчитан на запуск по расписанию: уже отправленные задачи
запоминаются в файле sent_tasks.json, повторно не отправляются.
Если новых задач на сегодня нет — сообщение не отправляется.
Список отправленных сбрасывается каждый день.

Запуск из корня проекта:
    python -m google_sheet_max            — обработать таблицы из config.json
    python -m google_sheet_max sheet.csv — вывести задачи из локального CSV
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
from datetime import date
from pathlib import Path
from typing import Any, cast

from google_sheet_max.max_bot import MaxApiError, MaxBot
from google_sheet_max.state_store import SentTasksStore
from google_sheet_max.tasks_report import TaskSheet, TasksReport

if sys.platform == "win32":
    cast("Any", sys.stdout).reconfigure(encoding="utf-8")

CONFIG_PATH = Path.cwd() / "config.json"
STATE_PATH = Path.cwd() / "sent_tasks.json"

# ошибки, при которых таблицу или отправку просто пропускаем
RECOVERABLE_ERRORS = (OSError, ValueError, urllib.error.URLError, MaxApiError)


def load_config() -> dict[str, Any]:
    """Читает config.json; завершает программу, если файла нет."""
    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
        config: dict[str, Any] = json.loads(text)
    except FileNotFoundError:
        print(f"Не найден файл конфигурации: {CONFIG_PATH}")
        sys.exit(1)
    return config


def create_bot(config: dict[str, Any]) -> MaxBot | None:
    """Создаёт бота MAX по токену из окружения или конфига."""
    token = os.environ.get("MAX_BOT_TOKEN") or config.get("max_bot_token")
    if not token:
        return None
    return MaxBot(str(token))


def main() -> None:
    """Точка входа."""
    today = date.today()

    if len(sys.argv) > 1:
        sheet = TaskSheet.from_csv_file(sys.argv[1])
        print(TasksReport(today, sheet.find_today_tasks(today)))
        return

    config = load_config()
    bot = create_bot(config)
    tables = config.get("tables", [])
    if not tables:
        print("В config.json не задан список таблиц (tables).")
        sys.exit(1)

    state = SentTasksStore(STATE_PATH, today)

    for table in tables:
        url = str(table.get("url", ""))
        chat_ids = [str(chat_id) for chat_id in table.get("chat_ids", [])]
        print(f"\nТаблица: {url}")
        try:
            sheet = TaskSheet(url)
            tasks = sheet.find_today_tasks(today)
        except RECOVERABLE_ERRORS as exc:
            print(f"  Не удалось загрузить таблицу: {exc}")
            continue
        print(TasksReport(today, tasks))
        if bot is None:
            print("  Токен бота не задан — сообщения не отправлены.")
            continue
        if sheet.csv_url is None:
            continue

        sheet_key = sheet.csv_url
        for chat_id in chat_ids:
            new_tasks = state.filter_new(sheet_key, chat_id, tasks)
            if not new_tasks:
                print(f"  Чат {chat_id}: новых задач нет, не отправляю.")
                continue
            report = TasksReport(
                today, new_tasks, title="Новые задачи"
            ).build()
            try:
                bot.send_message(chat_id, report)
            except RECOVERABLE_ERRORS as exc:
                print(f"  Не удалось отправить в чат {chat_id}: {exc}")
                continue
            state.mark_sent(sheet_key, chat_id, new_tasks)
            print(f"  Чат {chat_id}: отправлено {len(new_tasks)} новых задач.")

    state.save()


if __name__ == "__main__":
    main()
