"""Хранение истории отправленных задач (дедупликация).

История сохраняется в JSON-файл и привязана к дате: записи за прошлые дни
автоматически отбрасываются, поэтому каждое утро рассылка начинается заново.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from google_sheet_max.tasks_report import Task


class SentTasksStore:
    """Учёт задач, уже отправленных в чаты MAX.

    Структура хранилища:
    {
        "<ключ таблицы>": {
            "date": "2026-11-22",
            "sent": {"<chat_id>": ["<ключ задачи>", ...]}
        }
    }
    """

    def __init__(self, path: str | Path, today: date | None = None) -> None:
        """Загружает историю из файла, отбрасывая записи за прошлые дни."""
        self.path = Path(path)
        self.today = today or date.today()
        self._state: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            state: dict[str, Any] = json.loads(
                self.path.read_text(encoding="utf-8"),
            )
        except FileNotFoundError:
            return {}
        except ValueError as exc:
            backup = self.path.with_suffix(self.path.suffix + ".broken")
            self.path.replace(backup)
            print(
                f"ВНИМАНИЕ: {self.path} повреждён ({exc}).\n"
                f"Файл переименован в {backup}, история отправленных задач "
                "сброшена — все сегодняшние задачи будут отправлены заново.",
            )
            return {}
        # записи за прошлые дни больше не нужны
        today_key = self.today.isoformat()
        return {
            sheet_key: entry
            for sheet_key, entry in state.items()
            if entry.get("date") == today_key
        }

    def _entry(self, sheet_key: str) -> dict[str, Any]:
        entry: dict[str, Any] = self._state.setdefault(
            sheet_key, {"date": self.today.isoformat(), "sent": {}},
        )
        return entry

    def filter_new(
        self, sheet_key: str, chat_id: str, tasks: list[Task]
    ) -> list[Task]:
        """Возвращает ещё не отправленные в этот чат задачи таблицы."""
        sent_keys = set(self._entry(sheet_key)["sent"].get(str(chat_id), []))
        return [task for task in tasks if task.key not in sent_keys]

    def mark_sent(
        self, sheet_key: str, chat_id: str, tasks: list[Task]
    ) -> None:
        """Помечает задачи как отправленные в указанный чат."""
        sent = self._entry(sheet_key)["sent"].setdefault(str(chat_id), [])
        sent.extend(task.key for task in tasks)

    def save(self) -> None:
        """Сохраняет состояние на диск."""
        self.path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
