"""Тесты пакета google_sheet_max с полным покрытием кода."""

from __future__ import annotations

import io
import json
import runpy
import sys
import tempfile
import unittest
import urllib.error
from contextlib import ExitStack, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from google_sheet_max import main as main_module
from google_sheet_max.max_bot import MaxApiError, MaxBot
from google_sheet_max.state_store import SentTasksStore
from google_sheet_max.tasks_report import (
    Task,
    TaskSheet,
    TasksReport,
    parse_date,
)

TODAY = date.today()
TODAY_STR = TODAY.strftime("%d.%m.%Y")

HEADER = [
    "№ заявки / заказа",
    "Клиент",
    "Объект",
    "Ответственный",
    "Дедлайн",
    "Задача / этап",
    "Комментарий / причина ожидания",
    "Адрес замера / город",
    "Телефон клиента",
]

TASK_ROW = [
    "З-1",
    "Иван",
    "Кухня",
    "Степанова Елена",
    TODAY_STR,
    "Позвонить",
    "комментарий",
    "г. МВ",
    "8-900",
]


def make_task(**overrides: Any) -> Task:
    """Создаёт тестовую задачу с переопределяемыми полями."""
    fields = {
        "manager": "Степанова Елена",
        "id": "З-1",
        "client": "Иван",
        "phone": "8-900",
        "address": "г. МВ",
        "object": "Кухня",
        "stage": "Позвонить",
        "comment": "комментарий",
    }
    fields.update(overrides)
    return Task(**fields)


class FakeResponse:
    """Подделка ответа urllib с поддержкой with."""

    def __init__(self, body: bytes = b"{}", status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        """Возвращает тело ответа."""
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def http_error(code: int, body: bytes = b"{}") -> urllib.error.HTTPError:
    """Создаёт HTTPError с телом ответа."""
    return urllib.error.HTTPError(
        "http://x", code, "err", {}, io.BytesIO(body)  # type: ignore[arg-type]
    )


def write_csv(path: Path, rows: list[list[str]]) -> None:
    """Пишет CSV-файл со служебной строкой и заголовками."""
    lines = ["Название листа", ",".join(HEADER)]
    lines += [",".join(row) for row in rows]
    path.write_text("\n".join(lines), encoding="utf-8-sig")


class TestParseDate(unittest.TestCase):
    """Тесты разбора дат из ячеек таблицы."""

    def test_formats(self) -> None:
        cases = {
            "22.11.2026": date(2026, 11, 22),
            "22.11.2026 10:00": date(2026, 11, 22),
            "22/11/26": date(2026, 11, 22),
            "22/11/2026": date(2026, 11, 22),
            "22.11.26": date(2026, 11, 22),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_date(text), expected)

    def test_no_match(self) -> None:
        self.assertIsNone(parse_date("завтра"))
        self.assertIsNone(parse_date(""))

    def test_invalid_date(self) -> None:
        self.assertIsNone(parse_date("99.99.2026"))


class TestTask(unittest.TestCase):
    """Тесты модели задачи."""

    def test_key(self) -> None:
        task = make_task(id="З-1", object="Кухня", stage="Позвонить")
        self.assertEqual(task.key, "З-1|Кухня|Позвонить")


class TestTaskSheet(unittest.TestCase):
    """Тесты чтения листа таблицы."""

    SHEET_URL = (
        "https://docs.google.com/spreadsheets/d/ABC_123/edit?gid=42#gid=42"
    )

    def test_build_csv_url_with_gid(self) -> None:
        sheet = TaskSheet(self.SHEET_URL)
        self.assertEqual(
            sheet.csv_url,
            "https://docs.google.com/spreadsheets/d/ABC_123"
            "/export?format=csv&gid=42",
        )

    def test_build_csv_url_without_gid(self) -> None:
        sheet = TaskSheet("https://docs.google.com/spreadsheets/d/ABC_123/edit")
        self.assertTrue(sheet.csv_url.endswith("gid=0"))  # type: ignore[union-attr]

    def test_build_csv_url_invalid(self) -> None:
        with self.assertRaises(ValueError):
            TaskSheet("https://example.com/not-a-sheet")

    def test_read_csv_text_without_source(self) -> None:
        sheet = TaskSheet.from_csv_file("x.csv")
        sheet._local_csv_path = None
        with self.assertRaises(ValueError):
            sheet._read_csv_text()

    def test_load_rows_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sheet.csv"
            write_csv(path, [TASK_ROW])
            sheet = TaskSheet.from_csv_file(str(path))
            header, rows = sheet.load_rows()
        self.assertEqual(header, HEADER)
        self.assertEqual(rows, [TASK_ROW])

    def test_load_rows_header_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sheet.csv"
            path.write_text("a,b\n1,2\n", encoding="utf-8")
            sheet = TaskSheet.from_csv_file(str(path))
            with self.assertRaises(ValueError):
                sheet.load_rows()

    def test_load_rows_from_network(self) -> None:
        csv_text = "Название\n" + ",".join(HEADER) + "\n" + ",".join(TASK_ROW)
        response = FakeResponse(csv_text.encode("utf-8-sig"))
        with mock.patch(
            "urllib.request.urlopen", return_value=response
        ) as urlopen:
            sheet = TaskSheet(self.SHEET_URL)
            _header, rows = sheet.load_rows()
        urlopen.assert_called_once()
        self.assertEqual(rows, [TASK_ROW])

    def test_find_today_tasks_filters(self) -> None:
        yesterday = (TODAY - timedelta(days=1)).strftime("%d.%m.%Y")
        rows = [
            TASK_ROW,
            ["З-2", "П", "Шкаф", "Степанова Елена", yesterday],
            ["З-3", "Анна", "Стол", "", TODAY_STR, "x", "", "a", "1"],
            ["З-4", "Олег", "Стол", "Степанова Елена", TODAY_STR],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sheet.csv"
            write_csv(path, rows)
            tasks = TaskSheet.from_csv_file(str(path)).find_today_tasks(TODAY)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].id, "З-1")
        self.assertEqual(tasks[0].manager, "Степанова Елена")
        self.assertEqual(tasks[1].stage, "")  # короткая строка без колонок


class TestTasksReport(unittest.TestCase):
    """Тесты построения текстового отчёта."""

    def test_empty(self) -> None:
        report = TasksReport(TODAY, [])
        text = report.build()
        self.assertIn("На сегодня задач не запланировано.", text)
        self.assertNotIn("—", text.splitlines()[0])

    def test_with_tasks(self) -> None:
        tasks = [
            make_task(),
            make_task(id="З-2", comment=""),
        ]
        text = str(TasksReport(TODAY, tasks, title="Новые задачи"))
        self.assertIn("Новые задачи на", text)
        self.assertIn("Степанова Елена", text.splitlines()[0])
        self.assertIn("Всего задач: 2", text)
        self.assertIn("[З-1] Иван — 8-900", text)
        self.assertEqual(text.count("Комментарий"), 1)


class TestMaxBot(unittest.TestCase):
    """Тесты клиента Bot API MAX."""

    def test_empty_token(self) -> None:
        with self.assertRaises(ValueError):
            MaxBot("")

    def test_max_api_error(self) -> None:
        err = MaxApiError(404, "not found")
        self.assertIn("404", str(err))
        self.assertEqual(err.code, 404)
        self.assertEqual(err.body, "not found")

    def test_get_me_and_updates(self) -> None:
        bot = MaxBot("token")
        body = json.dumps({"user_id": 1}).encode()
        with mock.patch(
            "urllib.request.urlopen", return_value=FakeResponse(body)
        ) as urlopen:
            self.assertEqual(bot.get_me(), {"user_id": 1})
            self.assertEqual(bot.get_updates(), {"user_id": 1})
        self.assertEqual(urlopen.call_count, 2)
        request = urlopen.call_args[0][0]
        self.assertEqual(request.headers["Authorization"], "token")

    def test_http_error_raises_max_api_error(self) -> None:
        bot = MaxBot("token")
        with (
            mock.patch(
                "urllib.request.urlopen",
                side_effect=http_error(400, b"bad"),
            ),
            self.assertRaises(MaxApiError) as ctx,
        ):
            bot.get_me()
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(ctx.exception.body, "bad")

    def test_url_error_without_cert_issue(self) -> None:
        bot = MaxBot("token")
        with (
            mock.patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("timeout"),
            ),
            self.assertRaises(urllib.error.URLError),
        ):
            bot.get_me()

    def test_cert_error_retries_with_mincifra_context(self) -> None:
        bot = MaxBot("token")
        calls = []

        def fake_urlopen(request: object, **kwargs: Any) -> FakeResponse:
            calls.append(kwargs)
            if len(calls) == 1:
                reason = "CERTIFICATE_VERIFY_FAILED: unable to verify"
                raise urllib.error.URLError(reason)
            return FakeResponse(b"{}")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self.assertEqual(bot.get_me(), {})
        # первая попытка, 2 скачивания сертификата, повтор с контекстом
        self.assertEqual(len(calls), 4)
        self.assertIn("context", calls[-1])

    def test_ssl_context_loads_and_caches(self) -> None:
        bot = MaxBot("token")
        cert_body = (
            b"-----BEGIN CERTIFICATE-----\n-----END CERTIFICATE-----"
        )
        cert = FakeResponse(cert_body)
        with mock.patch(
            "urllib.request.urlopen", return_value=cert
        ) as urlopen:
            context1 = bot.ssl_context
            context2 = bot.ssl_context
        self.assertIs(context1, context2)
        self.assertEqual(urlopen.call_count, 2)

    def test_ssl_context_cert_download_failure(self) -> None:
        bot = MaxBot("token")
        with mock.patch(
            "urllib.request.urlopen", side_effect=OSError("no network")
        ):
            context = bot.ssl_context
        self.assertIsNotNone(context)

    def test_send_message_by_chat_id(self) -> None:
        bot = MaxBot("token")
        with mock.patch(
            "urllib.request.urlopen", return_value=FakeResponse(b"{}")
        ) as urlopen:
            bot.send_message(123, "привет")
        request = urlopen.call_args[0][0]
        self.assertIn("chat_id=123", request.full_url)
        self.assertEqual(json.loads(request.data), {"text": "привет"})

    def test_send_message_mocked_request(self) -> None:
        bot = MaxBot("token")
        with mock.patch.object(bot, "_request", return_value={}) as request:
            bot.send_message(123, "привет")
        request.assert_called_once_with(
            "POST", "/messages",
            params={"chat_id": "123"}, payload={"text": "привет"},
        )

    def test_send_message_fallback_to_user_id(self) -> None:
        bot = MaxBot("token")
        effects = [MaxApiError(404, "no chat"), {}]
        with mock.patch.object(
            bot, "_request", side_effect=effects
        ) as request:
            bot.send_message("123", "текст")
        self.assertEqual(
            [call.kwargs["params"] for call in request.call_args_list],
            [{"chat_id": "123"}, {"user_id": "123"}],
        )

    def test_send_message_non_404_error(self) -> None:
        bot = MaxBot("token")
        with (
            mock.patch.object(
                bot, "_request", side_effect=MaxApiError(400, "bad")
            ),
            self.assertRaises(MaxApiError),
        ):
            bot.send_message("123", "текст")

    def test_send_message_recipient_not_found(self) -> None:
        bot = MaxBot("token")
        with (
            mock.patch.object(
                bot, "_request", side_effect=MaxApiError(404, "no")
            ) as request,
            self.assertRaises(MaxApiError) as ctx,
        ):
            bot.send_message("123", "текст")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(ctx.exception.code, 404)

    def test_send_message_splits_long_text(self) -> None:
        bot = MaxBot("token")
        text = "а" * (MaxBot.MESSAGE_LIMIT + 10)
        with mock.patch.object(bot, "_request", return_value={}) as request:
            bot.send_message("123", text)
        self.assertEqual(request.call_count, 2)
        sizes = [
            len(call.kwargs["payload"]["text"])
            for call in request.call_args_list
        ]
        self.assertEqual(sizes, [MaxBot.MESSAGE_LIMIT, 10])


class TestSentTasksStore(unittest.TestCase):
    """Тесты хранилища отправленных задач."""

    def test_missing_file(self) -> None:
        store = SentTasksStore("нет-такого-файла.json", TODAY)
        tasks = store.filter_new("sheet", "1", [make_task()])
        self.assertEqual(len(tasks), 1)

    def test_broken_file_backed_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sent.json"
            path.write_text("{не json", encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                store = SentTasksStore(path, TODAY)
            self.assertIn("повреждён", out.getvalue())
            self.assertTrue(path.with_suffix(".json.broken").exists())
            task = make_task()
            self.assertEqual(store.filter_new("s", "1", [task]), [task])

    def test_old_entries_discarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sent.json"
            old = (TODAY - timedelta(days=1)).isoformat()
            data = {
                "sheet": {"date": old, "sent": {"1": ["З-1|Кухня|Позвонить"]}}
            }
            path.write_text(json.dumps(data), encoding="utf-8")
            store = SentTasksStore(path, TODAY)
            task = make_task()
            self.assertEqual(store.filter_new("sheet", "1", [task]), [task])

    def test_filter_mark_save_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sent.json"
            task1 = make_task()
            task2 = make_task(id="З-2")
            store = SentTasksStore(path, TODAY)
            self.assertEqual(
                store.filter_new("s", "1", [task1, task2]), [task1, task2]
            )
            store.mark_sent("s", "1", [task1])
            new = store.filter_new("s", "1", [task1, task2])
            self.assertEqual(new, [task2])
            self.assertEqual(
                store.filter_new("s", "2", [task1, task2]), [task1, task2]
            )
            store.save()
            reloaded = SentTasksStore(path, TODAY)
            self.assertEqual(
                reloaded.filter_new("s", "1", [task1, task2]), [task2]
            )


class TestMain(unittest.TestCase):
    """Тесты оркестрации в модуле main."""

    def run_main(
        self,
        tmp: str,
        config: dict[str, Any],
        sheet: mock.Mock | None = None,
        env_token: str | None = None,
        argv: list[str] | None = None,
        send_side_effect: object = None,
    ) -> tuple[str, mock.Mock]:
        """Запускает main() с конфигом и состоянием во временном каталоге."""
        config_path = Path(tmp) / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        state_path = Path(tmp) / "sent.json"
        env = {"MAX_BOT_TOKEN": env_token} if env_token else {}
        out = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(main_module, "CONFIG_PATH", config_path)
            )
            stack.enter_context(
                mock.patch.object(main_module, "STATE_PATH", state_path)
            )
            stack.enter_context(
                mock.patch.object(sys, "argv", argv or ["main"])
            )
            stack.enter_context(mock.patch.dict("os.environ", env, clear=True))
            bot_cls = stack.enter_context(
                mock.patch.object(main_module, "MaxBot")
            )
            if send_side_effect is not None:
                send_mock = bot_cls.return_value.send_message
                send_mock.side_effect = send_side_effect
            if sheet is not None:
                stack.enter_context(
                    mock.patch.object(
                        main_module, "TaskSheet", return_value=sheet
                    )
                )
            self.exit_code = None
            try:
                with redirect_stdout(out):
                    main_module.main()
            except SystemExit as exc:
                self.exit_code = exc.code
        return out.getvalue(), bot_cls

    def make_sheet(self, tasks: list[Task]) -> mock.Mock:
        """Создаёт мок листа с заданными задачами."""
        sheet = mock.Mock()
        sheet.csv_url = "sheet-url"
        sheet.find_today_tasks.return_value = tasks
        return sheet

    def test_local_csv_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sheet.csv"
            write_csv(path, [TASK_ROW])
            out = io.StringIO()
            with (
                mock.patch.object(sys, "argv", ["main", str(path)]),
                redirect_stdout(out),
            ):
                main_module.main()
        self.assertIn("[З-1] Иван", out.getvalue())

    def test_config_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            missing = Path(tmp) / "нет-файла.json"
            with (
                mock.patch.object(main_module, "CONFIG_PATH", missing),
                mock.patch.object(sys, "argv", ["main"]),
                redirect_stdout(out),
                self.assertRaises(SystemExit) as ctx,
            ):
                main_module.main()
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("Не найден файл конфигурации", out.getvalue())

    def test_no_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out, _ = self.run_main(tmp, {"tables": []})
        self.assertEqual(self.exit_code, 1)
        self.assertIn("не задан список таблиц", out)

    def test_table_load_error(self) -> None:
        sheet = self.make_sheet([])
        sheet.find_today_tasks.side_effect = ValueError("битая таблица")
        with tempfile.TemporaryDirectory() as tmp:
            out, _ = self.run_main(
                tmp,
                {"tables": [{"url": "u", "chat_ids": ["1"]}]},
                sheet=sheet,
                env_token="t",
            )
        self.assertIn("Не удалось загрузить таблицу", out)

    def test_no_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out, _ = self.run_main(
                tmp,
                {"tables": [{"url": "u", "chat_ids": ["1"]}]},
                sheet=self.make_sheet([make_task()]),
            )
        self.assertIn("Токен бота не задан", out)

    def test_send_new_and_skip_duplicates(self) -> None:
        sheet = self.make_sheet([make_task()])
        with tempfile.TemporaryDirectory() as tmp:
            config = {"tables": [{"url": "u", "chat_ids": ["1"]}]}
            out1, bot_cls = self.run_main(
                tmp, config, sheet=sheet, env_token="t"
            )
            out2, _ = self.run_main(tmp, config, sheet=sheet, env_token="t")
        self.assertIn("отправлено 1 новых задач", out1)
        self.assertIn("новых задач нет", out2)
        bot_cls.return_value.send_message.assert_called_once()
        payload = bot_cls.return_value.send_message.call_args[0][1]
        self.assertIn("Новые задачи", payload)

    def test_send_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out, _ = self.run_main(
                tmp,
                {"tables": [{"url": "u", "chat_ids": ["1"]}]},
                sheet=self.make_sheet([make_task()]),
                env_token="t",
                send_side_effect=MaxApiError(500, "down"),
            )
        self.assertIn("Не удалось отправить", out)

    def test_sheet_without_csv_url_skipped(self) -> None:
        sheet = self.make_sheet([make_task()])
        sheet.csv_url = None
        with tempfile.TemporaryDirectory() as tmp:
            _out, bot_cls = self.run_main(
                tmp,
                {"tables": [{"url": "u", "chat_ids": ["1"]}]},
                sheet=sheet,
                env_token="t",
            )
            bot_cls.return_value.send_message.assert_not_called()


class TestPackageEntryPoint(unittest.TestCase):
    """Тест точки входа python -m google_sheet_max."""

    def test_main_module_entry(self) -> None:
        with (
            mock.patch.object(main_module, "main") as main_fn,
            mock.patch.object(sys, "argv", ["main", "sheet.csv"]),
        ):
            runpy.run_module(
                "google_sheet_max.__main__", run_name="__main__"
            )
        main_fn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
