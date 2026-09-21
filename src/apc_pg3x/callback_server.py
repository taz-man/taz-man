"""Threaded, size-bounded HTTP adapter for Ayla device callbacks."""

from __future__ import annotations

import json
import logging
import socket
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore, Thread, Timer

from .device import RoutingError
from .protocol import AuthenticationError, AylaCallbackProtocol
from .transport import ProtocolError

_LOGGER = logging.getLogger(__name__)


def _diagnostic_stage(path: str) -> str:
    if path == "/local_lan/key_exchange.json":
        return "KEY_EXCHANGE"
    if path == "/local_lan/property/datapoint.json":
        return "AUTHENTICATED_DATAPOINT"
    if path == "/local_lan/commands.json":
        return "COMMAND_FETCH"
    return "CALLBACK"


class AylaCallbackServer:
    """Expose the three Ayla callback endpoints with bounded concurrency."""

    def __init__(
        self,
        protocol: AylaCallbackProtocol,
        *,
        host: str = "0.0.0.0",
        port: int = 10275,
        max_body: int = 65536,
        max_workers: int = 16,
        read_timeout: float = 3.0,
        request_deadline: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_body < 256:
            raise ValueError("max_body is too small")
        if max_workers < 1 or read_timeout <= 0 or request_deadline <= 0:
            raise ValueError("worker count and request timeouts must be positive")
        self._protocol = protocol
        self._max_body = max_body
        self._read_timeout = read_timeout
        self._request_deadline = request_deadline
        self._clock = clock
        adapter = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "AylaCallback/1"
            sys_version = ""

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(adapter._read_timeout)
                self._deadline_timer = Timer(
                    adapter._request_deadline, self._expire_request
                )
                self._deadline_timer.daemon = True
                self._deadline_timer.start()

            def finish(self) -> None:
                self._deadline_timer.cancel()
                super().finish()

            def _expire_request(self) -> None:
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    return

            def do_POST(self) -> None:
                adapter._post(self)

            def do_GET(self) -> None:
                adapter._get(self)

            def log_message(self, _format: str, *args: object) -> None:
                return

        class BoundedThreadingHTTPServer(ThreadingHTTPServer):
            def __init__(self, server_address, request_handler_class):
                self._worker_slots = BoundedSemaphore(max_workers)
                super().__init__(server_address, request_handler_class)

            def process_request(self, request, client_address) -> None:
                if not self._worker_slots.acquire(blocking=False):
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, client_address)
                except BaseException:
                    self._worker_slots.release()
                    raise

            def process_request_thread(self, request, client_address) -> None:
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    self._worker_slots.release()

            def handle_error(self, request, client_address) -> None:
                return

        self._server = BoundedThreadingHTTPServer((host, port), Handler)
        self._server.daemon_threads = True
        self._thread: Thread | None = None

    @property
    def server_address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("callback server already started")
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=5)
            self._thread = None
        self._server.server_close()

    def _post(self, handler: BaseHTTPRequestHandler) -> None:
        body = self._read_body(handler)
        if body is None:
            return
        source = handler.client_address[0]
        try:
            if handler.path == "/local_lan/key_exchange.json":
                request = self._parse_key_exchange(body)
                response = self._protocol.key_exchange(
                    key_id=request["key_id"],
                    source_address=source,
                    device_random=request["random_1"],
                    device_time=request["time_1"],
                    now=self._clock(),
                )
                _LOGGER.info("Ayla LAN diagnostic stage=KEY_EXCHANGE reason=ACCEPTED")
                self._json_response(handler, 200, response)
                return
            if handler.path == "/local_lan/property/datapoint.json":
                key_id = self._protocol.key_id_for_source(source)
                self._protocol.receive_datapoint(
                    key_id=key_id,
                    source_address=source,
                    body=body,
                    now=self._clock(),
                )
                self._json_response(handler, 200, {"status": "success"})
                return
            self._json_response(handler, 404, {"error": "not found"})
        except RoutingError:
            _LOGGER.warning("Ayla LAN diagnostic stage=SOURCE_ROUTING reason=REJECTED")
            self._json_response(handler, 400, {"error": "invalid request"})
        except AuthenticationError:
            _LOGGER.warning(
                "Ayla LAN diagnostic stage=%s reason=AUTHENTICATION_REJECTED",
                _diagnostic_stage(handler.path),
            )
            self._json_response(handler, 400, {"error": "invalid request"})
        except (ProtocolError, ValueError, TypeError):
            _LOGGER.warning(
                "Ayla LAN diagnostic stage=%s reason=MALFORMED",
                _diagnostic_stage(handler.path),
            )
            self._json_response(handler, 400, {"error": "invalid request"})

    def _get(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.path != "/local_lan/commands.json":
            self._json_response(handler, 404, {"error": "not found"})
            return
        source = handler.client_address[0]
        try:
            key_id = self._protocol.key_id_for_source(source)
            body = self._protocol.fetch_commands(
                key_id=key_id, source_address=source, now=self._clock()
            )
        except (AuthenticationError, ProtocolError, RoutingError, ValueError, TypeError):
            self._json_response(handler, 400, {"error": "invalid request"})
            return
        self._bytes_response(handler, 200, body, "application/json")

    def _read_body(self, handler: BaseHTTPRequestHandler) -> bytes | None:
        raw_length = handler.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            handler.close_connection = True
            self._json_response(handler, 411, {"error": "content length required"})
            return None
        if length > self._max_body:
            handler.close_connection = True
            self._json_response(handler, 413, {"error": "body too large"})
            return None
        try:
            body = handler.rfile.read(length)
        except TimeoutError:
            handler.close_connection = True
            self._json_response(handler, 408, {"error": "request timeout"})
            return None
        if len(body) != length:
            handler.close_connection = True
            self._json_response(handler, 400, {"error": "invalid request"})
            return None
        return body

    @staticmethod
    def _parse_key_exchange(body: bytes) -> dict[str, object]:
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict) or set(payload) != {"key_exchange"}:
                raise ValueError
            exchange = payload["key_exchange"]
            if not isinstance(exchange, dict) or set(exchange) != {
                "ver",
                "proto",
                "key_id",
                "random_1",
                "time_1",
            }:
                raise ValueError
            key_id = exchange["key_id"]
            random_value = exchange["random_1"]
            device_time = exchange["time_1"]
            if exchange["ver"] != 1 or exchange["proto"] != 1:
                raise ValueError
            if not isinstance(key_id, str) or not key_id:
                raise ValueError
            if (
                not isinstance(random_value, str)
                or len(random_value) != 16
                or not random_value.isalnum()
                or type(device_time) is not int
                or not 0 <= device_time < 2**63
            ):
                raise ValueError
            return {"key_id": key_id, "random_1": random_value, "time_1": device_time}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ProtocolError("malformed key exchange") from None

    @classmethod
    def _json_response(
        cls, handler: BaseHTTPRequestHandler, status: int, payload: dict[str, object]
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        cls._bytes_response(handler, status, body, "application/json")

    @staticmethod
    def _bytes_response(
        handler: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str
    ) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
