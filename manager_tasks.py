"""Отправляет менеджерам их задачи на сегодня из Google-таблиц в мессенджер MAX.

Задачей на сегодня считается строка, у которой дедлайн приходится на
сегодняшнее число (состояние «Сегодня»).

Настройки задаются в файле config.json рядом со скриптом:

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

Запуск:
    python manager_tasks.py                  — обработать все таблицы из config.json
    python manager_tasks.py sheet.csv        — вывести задачи из локального CSV (без отправки)
"""

import csv
import io
import json
import os
import re
import ssl
import sys
import urllib.request
import urllib.error
from datetime import date, datetime

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

MAX_API_URL = "https://platform-api2.max.ru/messages"
MAX_MESSAGE_LIMIT = 4000  # лимит длины сообщения в MAX

# Сертификаты НУЦ Минцифры — нужны для проверки TLS platform-api2.max.ru
MINCIFRA_CERT_URLS = [
    "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt",
    "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt",
]

DATE_PATTERNS = ["%d.%m.%Y", "%d/%m/%y", "%d/%m/%Y", "%d.%m.%y"]


def parse_date(value: str):
    match = re.search(r"\d{2}[./]\d{2}[./]\d{2,4}", value)
    if not match:
        return None
    text = match.group(0)
    for pattern in DATE_PATTERNS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def sheet_csv_url(url: str) -> str:
    """Строит URL экспорта CSV из ссылки на Google-таблицу."""
    spreadsheet = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url)
    if not spreadsheet:
        raise ValueError(f"не удалось извлечь ID таблицы из ссылки: {url}")
    gid = re.search(r"[#?&]gid=(\d+)", url)
    return (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet.group(1)}"
        f"/export?format=csv&gid={gid.group(1) if gid else '0'}"
    )


def load_rows(url):
    """Скачивает лист Google-таблицы в CSV и находит строку заголовков."""
    request = urllib.request.Request(
        sheet_csv_url(url), headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(request) as response:
        text = response.read().decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    # заголовки — строка, содержащая колонку «№ заявки / заказа»
    for i, row in enumerate(rows):
        if any(cell.strip().startswith("№ заявки") for cell in row):
            return row, rows[i + 1 :]
    raise ValueError("не найдена строка заголовков («№ заявки / заказа»)")


def find_today_tasks(header, rows, today):
    """Выбирает из таблицы задачи с дедлайном на сегодня."""
    col = {name.strip(): i for i, name in enumerate(header)}

    def cell(row, name):
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
            {
                "manager": cell(row, "Ответственный"),
                "id": cell(row, "№ заявки / заказа"),
                "client": cell(row, "Клиент"),
                "phone": cell(row, "Телефон клиента"),
                "address": cell(row, "Адрес замера / город"),
                "object": cell(row, "Объект"),
                "stage": cell(row, "Задача / этап"),
                "comment": cell(row, "Комментарий / причина ожидания"),
            }
        )
    return tasks


def build_report(today, tasks):
    manager = tasks[0]["manager"] if tasks else ""
    lines = [
        f"Задачи на {today.strftime('%d.%m.%Y')}"
        + (f" — {manager}" if manager else ""),
        "=" * 50,
    ]
    if not tasks:
        lines.append("На сегодня задач не запланировано.")
        return "\n".join(lines)
    lines.append(f"Всего задач: {len(tasks)}")
    for task in tasks:
        lines.append("")
        lines.append(f"[{task['id']}] {task['client']} — {task['phone']}")
        lines.append(f"  Задача: {task['stage']}")
        lines.append(f"  Объект: {task['object']}")
        lines.append(f"  Адрес: {task['address']}")
        if task["comment"]:
            lines.append(f"  Комментарий: {task['comment']}")
    return "\n".join(lines)


def _max_ssl_context():
    """SSL-контекст для MAX API с сертификатами Минцифры."""
    context = ssl.create_default_context()
    for url in MINCIFRA_CERT_URLS:
        try:
            with urllib.request.urlopen(url) as response:
                context.load_verify_locations(cadata=response.read().decode("utf-8"))
        except Exception:
            pass  # попробуем системные сертификаты
    return context


def _max_request(request):
    try:
        return urllib.request.urlopen(request)
    except urllib.error.URLError as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc.reason):
            raise
        context = _max_ssl_context()
        return urllib.request.urlopen(request, context=context)


def send_to_max(text: str, token: str, chat_id: str):
    """Отправляет текст в чат MAX через Bot API."""
    chunks = [
        text[i : i + MAX_MESSAGE_LIMIT]
        for i in range(0, len(text), MAX_MESSAGE_LIMIT)
    ]
    for chunk in chunks:
        body = json.dumps({"text": chunk}).encode("utf-8")
        for param in ("chat_id", "user_id"):
            url = f"{MAX_API_URL}?{param}={chat_id}"
            request = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": token,
                },
            )
            try:
                with _max_request(request) as response:
                    if response.status == 200:
                        break
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", "replace")
                if exc.code == 404 and param == "chat_id":
                    continue  # попробуем отправить по user_id
                raise RuntimeError(f"MAX API вернул {exc.code}: {error_body}")
        else:
            raise RuntimeError(
                f"чат {chat_id} не найден ни по chat_id, ни по user_id"
            )


def process_local_csv(csv_path, today):
    """Режим отладки: печатает задачи из локального CSV без отправки."""
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    for i, row in enumerate(rows):
        if any(cell.strip().startswith("№ заявки") for cell in row):
            header, data = row, rows[i + 1 :]
            break
    else:
        raise ValueError("не найдена строка заголовков («№ заявки / заказа»)")
    print(build_report(today, find_today_tasks(header, data, today)))


def main():
    today = date.today()

    if len(sys.argv) > 1:
        process_local_csv(sys.argv[1], today)
        return

    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"Не найден файл конфигурации: {CONFIG_PATH}")
        sys.exit(1)

    token = os.environ.get("MAX_BOT_TOKEN") or config.get("max_bot_token")
    tables = config.get("tables", [])
    if not tables:
        print("В config.json не задан список таблиц (tables).")
        sys.exit(1)

    for table in tables:
        url = table.get("url", "")
        chat_ids = [str(c) for c in table.get("chat_ids", [])]
        print(f"\nТаблица: {url}")
        try:
            header, rows = load_rows(url)
            tasks = find_today_tasks(header, rows, today)
        except Exception as exc:
            print(f"  Не удалось загрузить таблицу: {exc}")
            continue
        report = build_report(today, tasks)
        print(report)
        if not token:
            print("  Токен бота не задан — сообщения не отправлены.")
            continue
        for chat_id in chat_ids:
            try:
                send_to_max(report, token, chat_id)
                print(f"  Отправлено в чат {chat_id}.")
            except Exception as exc:
                print(f"  Не удалось отправить в чат {chat_id}: {exc}")


if __name__ == "__main__":
    main()
