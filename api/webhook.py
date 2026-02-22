"""
api/webhook.py — Этот файл НОВЫЙ. Создайте папку api/ в корне проекта и положите туда этот файл.

Vercel ищет файлы в папке api/ и автоматически делает из них HTTP-эндпоинты.
Этот файл будет доступен по адресу: https://ВАШ-ДОМЕН.vercel.app/api/webhook
Именно на этот адрес Telegram будет присылать сообщения.
"""

import json
import asyncio
import logging
import sys
import os

# Добавляем корень проекта в путь Python, чтобы найти main.py, config.py и т.д.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from http.server import BaseHTTPRequestHandler

logger = logging.getLogger(__name__)


class handler(BaseHTTPRequestHandler):
    """
    Vercel вызывает класс handler для каждого входящего HTTP-запроса.
    Telegram присылает POST-запросы с данными о новых сообщениях.
    """

    def do_POST(self):
        """Обрабатываем POST-запрос от Telegram"""
        try:
            # Читаем тело запроса
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self._send_response(400, "Empty body")
                return

            body = self.rfile.read(content_length)
            update_data = json.loads(body.decode('utf-8'))

            # ✅ Импортируем handle_update из main.py и запускаем
            from main import handle_update
            asyncio.run(handle_update(update_data))

            # Отвечаем Telegram "всё хорошо"
            self._send_response(200, "OK")

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from Telegram: {e}")
            self._send_response(400, "Invalid JSON")
        except Exception as e:
            logger.error(f"Webhook handler error: {e}", exc_info=True)
            self._send_response(500, f"Internal error: {str(e)}")

    def do_GET(self):
        """
        GET-запрос — просто проверка, что webhook работает.
        Откройте https://ВАШ-ДОМЕН.vercel.app/api/webhook в браузере — должно показать статус.
        """
        self._send_response(200, "Telegram Bot Webhook is active ✅")

    def _send_response(self, status_code: int, message: str):
        """Вспомогательная функция для отправки ответа"""
        self.send_response(status_code)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(message.encode('utf-8'))

    def log_message(self, format, *args):
        """Перенаправляем логи Vercel в стандартный logger"""
        logger.info(f"Webhook: {format % args}")
