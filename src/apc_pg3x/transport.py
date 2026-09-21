"""Bounded outbound HTTP transport for Ayla LAN registration."""

from __future__ import annotations

import logging
from queue import Empty, Queue
from threading import Thread
from typing import Protocol

import requests

_LOGGER = logging.getLogger(__name__)


class ProtocolError(RuntimeError):
    """A sanitized Ayla LAN protocol or transport failure."""


class HttpClient(Protocol):
    def post(self, url: str, *, json: object, timeout: tuple[float, float]): ...


class AylaLanTransport:
    """Send bounded Ayla LAN registration requests.

    Dynamic response bodies, addresses, credentials, and exception text are
    deliberately excluded from log messages and raised exceptions.
    """

    def __init__(self, *, http: HttpClient | None = None, request_timeout: float = 3.0) -> None:
        if not 0 < request_timeout <= 3.0:
            raise ValueError("request_timeout must be greater than zero and at most 3 seconds")
        self._http = http or requests.Session()
        self.request_timeout = float(request_timeout)

    def register(
        self,
        *,
        address: str,
        callback_host: str,
        callback_port: int,
        notify: bool,
    ) -> None:
        if not address or not callback_host:
            raise ValueError("address and callback_host are required")
        if not 1 <= callback_port <= 65535:
            raise ValueError("callback_port is out of range")
        payload = {
            "local_reg": {
                "ip": callback_host,
                "port": callback_port,
                "uri": "/local_lan",
                "notify": int(notify),
            }
        }
        result: Queue[str] = Queue(maxsize=1)
        worker = Thread(
            target=self._perform_registration,
            args=(f"http://{address}/local_reg.json", payload, result),
            daemon=True,
        )
        worker.start()
        try:
            outcome = result.get(timeout=self.request_timeout)
        except Empty:
            self._close_http()
            _LOGGER.warning("Ayla LAN diagnostic stage=REGISTRATION reason=DEADLINE")
            raise ProtocolError("registration failed") from None
        if outcome == "transport":
            _LOGGER.warning("Ayla LAN diagnostic stage=REGISTRATION reason=TRANSPORT")
            raise ProtocolError("registration failed")
        if outcome == "rejected":
            _LOGGER.warning("Ayla LAN diagnostic stage=REGISTRATION reason=REJECTED")
            raise ProtocolError("registration rejected")
        _LOGGER.info("Ayla LAN diagnostic stage=REGISTRATION reason=ACCEPTED")

    def _perform_registration(
        self, url: str, payload: object, result: Queue[str]
    ) -> None:
        try:
            response = self._http.post(
                url,
                json=payload,
                timeout=(self.request_timeout, self.request_timeout),
            )
            if response.status_code != 202:
                result.put("rejected")
                return
        except Exception:  # noqa: BLE001 - report only a sanitized outcome
            result.put("transport")
            return
        result.put("success")

    def _close_http(self) -> None:
        close = getattr(self._http, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception:  # noqa: BLE001 - never expose close failure details
            return
