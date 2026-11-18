"""
HTTP-сервер здоровья для Render.

Бесплатный Web Service на Render требует открытый порт, иначе процесс
считается упавшим. Единственный эндпоинт отвечает 200 OK на любой GET,
ничего не раздаёт с диска и ничего не пишет в лог.
"""

import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger(__name__)

DEFAULT_PORT = 10000  # Render передаёт номер порта в переменной окружения PORT


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Отвечает 200 OK на проверки Render, не раздавая файлы с диска"""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        """
        HEAD без тела: UptimeRobot и подобные мониторы пингуют именно им.
        Без обработчика BaseHTTPRequestHandler отвечал 501, и Render
        считал сервис нездоровым.
        """
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def run_health_server() -> None:
    """
    Поднимает блокирующий сервер в отдельном потоке (daemon).

    Поток daemon: непойманное исключение убило бы его молча — Render стал бы
    считать сервис упавшим без единой строки в логе. Ловим ошибку явно.
    """
    try:
        port = int(os.getenv("PORT", DEFAULT_PORT))
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    except Exception:
        logger.exception("Failed to run health-check server")
        return
    logger.info("Background web server launched on port %s", port)
    try:
        server.serve_forever()
    except Exception:
        logger.exception("Health-check server went down")
