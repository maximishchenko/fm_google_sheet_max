"""Клиент для Bot API мессенджера MAX.

Домен API использует TLS-сертификат НУЦ Минцифры — клиент сам скачивает
корневой и промежуточный сертификаты с gu-st.ru, если системные не подходят.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any

# Сертификаты НУЦ Минцифры — нужны для проверки TLS platform-api2.max.ru
MINCIFRA_CERT_URLS = (
    "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt",
    "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt",
)

HTTP_NOT_FOUND = 404


class MaxApiError(Exception):
    """Ошибка вызова MAX Bot API с HTTP-кодом и телом ответа."""

    def __init__(self, code: int, body: str) -> None:
        """Создаёт ошибку с HTTP-кодом и телом ответа."""
        super().__init__(f"MAX API вернул {code}: {body}")
        self.code = code
        self.body = body


class MaxBot:
    """Бот мессенджера MAX: отправка сообщений и получение обновлений."""

    API_BASE = "https://platform-api2.max.ru"
    MESSAGE_LIMIT = 4000  # лимит длины сообщения в MAX

    def __init__(self, token: str) -> None:
        """Создаёт бота с указанным токеном."""
        if not token:
            msg = "не задан токен бота MAX"
            raise ValueError(msg)
        self.token = token
        self._ssl_context: ssl.SSLContext | None = None

    @property
    def ssl_context(self) -> ssl.SSLContext:
        """SSL-контекст с сертификатами Минцифры (ленивая инициализация)."""
        if self._ssl_context is None:
            context = ssl.create_default_context()
            for url in MINCIFRA_CERT_URLS:
                try:
                    # URL сертификатов задан константой, схема https
                    with urllib.request.urlopen(url) as response:  # nosec B310
                        context.load_verify_locations(
                            cadata=response.read().decode("utf-8"),
                        )
                except (OSError, ssl.SSLError, ValueError):
                    pass  # попробуем системные сертификаты
            self._ssl_context = context
        return self._ssl_context

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self.API_BASE + path
        if params:
            query = "&".join(f"{key}={value}" for key, value in params.items())
            url = f"{url}?{query}"
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": self.token,
            },
            method=method,
        )
        try:
            try:
                # URL API задан константой класса, схема https
                response = urllib.request.urlopen(request)  # nosec B310
            except urllib.error.URLError as exc:
                if "CERTIFICATE_VERIFY_FAILED" not in str(exc.reason):
                    raise
                response = urllib.request.urlopen(  # nosec B310
                    request, context=self.ssl_context
                )
        except urllib.error.HTTPError as exc:
            raise MaxApiError(
                exc.code,
                exc.read().decode("utf-8", "replace"),
            ) from exc
        with response:
            body = response.read()
        result: dict[str, Any] = json.loads(body) if body else {}
        return result

    def get_me(self) -> dict[str, Any]:
        """Возвращает информацию о боте."""
        return self._request("GET", "/me")

    def get_updates(self) -> dict[str, Any]:
        """Возвращает новые события бота (сообщения и т.п.)."""
        return self._request("GET", "/updates")

    def send_message(self, chat_id: object, text: str) -> None:
        """Отправляет текст в чат.

        Получатель передаётся как chat_id или user_id — при 404 на chat_id
        автоматически пробуется user_id. Длинные тексты делятся на части.
        """
        recipient = str(chat_id)
        chunks = [
            text[i : i + self.MESSAGE_LIMIT]
            for i in range(0, len(text), self.MESSAGE_LIMIT)
        ]
        for chunk in chunks:
            for param in ("chat_id", "user_id"):
                try:
                    self._request(
                        "POST",
                        "/messages",
                        params={param: recipient},
                        payload={"text": chunk},
                    )
                    break
                except MaxApiError as exc:
                    if exc.code == HTTP_NOT_FOUND and param == "chat_id":
                        continue  # попробуем отправить по user_id
                    raise
