"""Получение задач менеджеров из Google-таблицы и построение текстового отчёта.

Таблица скачивается в формате CSV (прямой экспорт Google Sheets, без
API-ключей). Задачей на сегодня считается строка, у которой дедлайн
приходится на сегодняшнее число (состояние «Сегодня»).
"""

from __future__ import annotations

import csv
import io
import re
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

DATE_PATTERNS = ("%d.%m.%Y", "%d/%m/%y", "%d/%m/%Y", "%d.%m.%y")
_DATE_RE = re.compile(r"\d{2}[./]\d{2}[./]\d{2,4}")


def parse_date(value: str) -> date | None:
    """Извлекает дату из ячейки вида '22.11.2026' или '22/11/26'."""
    match = _DATE_RE.search(value)
    if not match:
        return None
    text = match.group(0)
    for pattern in DATE_PATTERNS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


@dataclass
class Task:
    """Задача менеджера — одна строка таблицы."""

    manager: str
    id: str
    client: str
    phone: str
    address: str
    object: str
    stage: str
    comment: str

    @property
    def key(self) -> str:
        """Уникальный ключ задачи для дедупликации отправленных.

        Составной, т.к. в таблице бывают строки с одинаковым № заявки,
        но разными изделиями и этапами.
        """
        return f"{self.id}|{self.object}|{self.stage}"


class TaskSheet:
    """Лист Google-таблицы с задачами одного менеджера."""

    def __init__(self, url: str, local_csv_path: Path | None = None) -> None:
        """Создаёт лист по ссылке или по локальному CSV-файлу."""
        self.url = url
        self.csv_url = None if local_csv_path else self._build_csv_url(url)
        self._local_csv_path = local_csv_path

    @classmethod
    def from_csv_file(cls, csv_path: str) -> TaskSheet:
        """Создаёт лист, читаемый из локального CSV-файла (режим отладки)."""
        return cls(csv_path, local_csv_path=Path(csv_path))

    @staticmethod
    def _build_csv_url(url: str) -> str:
        spreadsheet = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url)
        if not spreadsheet:
            msg = f"не удалось извлечь ID таблицы из ссылки: {url}"
            raise ValueError(msg)
        gid = re.search(r"[#?&]gid=(\d+)", url)
        return (
            f"https://docs.google.com/spreadsheets/d/{spreadsheet.group(1)}"
            f"/export?format=csv&gid={gid.group(1) if gid else '0'}"
        )

    def _read_csv_text(self) -> str:
        if self._local_csv_path is not None:
            return self._local_csv_path.read_text(encoding="utf-8-sig")
        if self.csv_url is None:
            msg = "не задан источник данных таблицы"
            raise ValueError(msg)
        request = urllib.request.Request(
            self.csv_url, headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(request) as response:  # nosec B310
            text: str = response.read().decode("utf-8-sig")
            return text

    def load_rows(self) -> tuple[list[str], list[list[str]]]:
        """Читает лист и возвращает заголовки и строки данных."""
        rows = list(csv.reader(io.StringIO(self._read_csv_text())))
        # заголовки — строка, содержащая колонку «№ заявки / заказа»
        for i, row in enumerate(rows):
            if any(cell.strip().startswith("№ заявки") for cell in row):
                return row, rows[i + 1 :]
        msg = "не найдена строка заголовков («№ заявки / заказа»)"
        raise ValueError(msg)

    def find_today_tasks(self, today: date) -> list[Task]:
        """Возвращает список задач с дедлайном на указанную дату."""
        header, rows = self.load_rows()
        col = {name.strip(): i for i, name in enumerate(header)}

        def cell(row: list[str], name: str) -> str:
            idx = col.get(name)
            if idx is None or idx >= len(row):
                return ""
            return row[idx].strip()

        tasks = []
        for row in rows:
            if not cell(row, "Ответственный"):
                continue
            if parse_date(cell(row, "Дедлайн")) != today:
                continue
            tasks.append(
                Task(
                    manager=cell(row, "Ответственный"),
                    id=cell(row, "№ заявки / заказа"),
                    client=cell(row, "Клиент"),
                    phone=cell(row, "Телефон клиента"),
                    address=cell(row, "Адрес замера / город"),
                    object=cell(row, "Объект"),
                    stage=cell(row, "Задача / этап"),
                    comment=cell(row, "Комментарий / причина ожидания"),
                ),
            )
        return tasks


class TasksReport:
    """Текстовый отчёт по списку задач."""

    def __init__(
        self, today: date, tasks: list[Task], title: str = "Задачи",
    ) -> None:
        """Создаёт отчёт по задачам на указанную дату."""
        self.today = today
        self.tasks = list(tasks)
        self.title = title

    def build(self) -> str:
        """Формирует текст отчёта."""
        manager = self.tasks[0].manager if self.tasks else ""
        header = f"{self.title} на {self.today.strftime('%d.%m.%Y')}"
        if manager:
            header += f" — {manager}"
        lines = [header, "=" * 50]
        if not self.tasks:
            lines.append("На сегодня задач не запланировано.")
            return "\n".join(lines)
        lines.append(f"Всего задач: {len(self.tasks)}")
        for task in self.tasks:
            lines.append("")
            lines.append(f"[{task.id}] {task.client} — {task.phone}")
            lines.append(f"  Задача: {task.stage}")
            lines.append(f"  Объект: {task.object}")
            lines.append(f"  Адрес: {task.address}")
            if task.comment:
                lines.append(f"  Комментарий: {task.comment}")
        return "\n".join(lines)

    def __str__(self) -> str:
        """Возвращает текст отчёта."""
        return self.build()
