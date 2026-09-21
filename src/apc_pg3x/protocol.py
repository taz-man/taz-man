"""Authenticated Ayla callback wire protocol.

The exchange derivation and CBC message framing are isolated here so sanitized
hardware fixtures can replace them if protocol assumptions are corrected.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

from Crypto.Cipher import AES

from .device import AylaLanRouter, RoutingError
from .transport import ProtocolError

_LOGGER = logging.getLogger(__name__)


class AuthenticationError(ProtocolError):
    """An authenticated callback envelope was invalid or replayed."""


@dataclass(frozen=True, slots=True)
class _DirectionalKeys:
    encryption: bytes
    signing: bytes
    iv: bytes


class SessionCodec:
    """Encode the observed Ayla LAN stream in both session directions."""

    def __init__(
        self,
        *,
        controller_keys: _DirectionalKeys,
        device_keys: _DirectionalKeys,
        max_body: int,
    ) -> None:
        if max_body < 256:
            raise ValueError("max_body is too small")
        self._controller_keys = controller_keys
        self._device_keys = device_keys
        self._max_body = max_body
        self._lock = RLock()
        self._controller_receive_sequence = 0
        self._device_receive_sequence = 0
        self._controller_encrypt_iv = controller_keys.iv
        self._controller_decrypt_iv = controller_keys.iv
        self._device_encrypt_iv = device_keys.iv
        self._device_decrypt_iv = device_keys.iv

    @classmethod
    def from_exchange(
        cls,
        shared_key: str,
        *,
        device_random: str,
        device_time: int,
        controller_random: str,
        controller_time: int,
        max_body: int = 65536,
    ) -> SessionCodec:
        if not isinstance(shared_key, str) or not shared_key or len(shared_key) > 4096:
            raise AuthenticationError("invalid session key")
        if (
            not isinstance(device_random, str)
            or not isinstance(controller_random, str)
            or not device_random
            or not controller_random
            or len(device_random) > 64
            or len(controller_random) > 64
        ):
            raise AuthenticationError("invalid key exchange")
        if (
            type(device_time) is not int
            or type(controller_time) is not int
            or not 0 <= device_time < 2**63
            or not 0 <= controller_time < 2**63
        ):
            raise AuthenticationError("invalid key exchange")
        try:
            key = shared_key.encode("utf-8")
            device_random.encode("ascii")
            controller_random.encode("ascii")
        except UnicodeError:
            raise AuthenticationError("invalid key exchange") from None
        return cls(
            controller_keys=cls._derive_direction(
                key,
                device_random,
                controller_random,
                device_time,
                controller_time,
            ),
            device_keys=cls._derive_direction(
                key,
                controller_random,
                device_random,
                controller_time,
                device_time,
            ),
            max_body=max_body,
        )

    @classmethod
    def _derive_direction(
        cls,
        key: bytes,
        first_random: str,
        second_random: str,
        first_time: int,
        second_time: int,
    ) -> _DirectionalKeys:
        transcript = (
            first_random.encode("ascii")
            + second_random.encode("ascii")
            + str(first_time).encode("ascii")
            + str(second_time).encode("ascii")
        )
        return _DirectionalKeys(
            signing=cls._double_hmac(key, transcript + b"0"),
            encryption=cls._double_hmac(key, transcript + b"1"),
            iv=cls._double_hmac(key, transcript + b"2")[: AES.block_size],
        )

    @staticmethod
    def _double_hmac(key: bytes, data: bytes) -> bytes:
        first = hmac.new(key, data, hashlib.sha256).digest()
        return hmac.new(key, first + data, hashlib.sha256).digest()

    def seal_controller(self, plaintext: bytes) -> bytes:
        with self._lock:
            envelope, self._controller_encrypt_iv = self._seal(
                self._controller_keys, plaintext, self._controller_encrypt_iv
            )
            return envelope

    def seal_device(self, plaintext: bytes) -> bytes:
        with self._lock:
            envelope, self._device_encrypt_iv = self._seal(
                self._device_keys, plaintext, self._device_encrypt_iv
            )
            return envelope

    def open_controller(self, body: bytes) -> bytes:
        with self._lock:
            plaintext, next_iv = self._open(
                self._controller_keys, body, self._controller_decrypt_iv
            )
            self._controller_decrypt_iv = next_iv
            sequence = self._sequence(plaintext, self._controller_receive_sequence)
            self._controller_receive_sequence = sequence
            return plaintext

    def open_device(self, body: bytes) -> bytes:
        plaintext, sequence = self.verify_device(body)
        self.commit_device(sequence)
        return plaintext

    def verify_device(self, body: bytes) -> tuple[bytes, int]:
        with self._lock:
            plaintext, next_iv = self._open(
                self._device_keys, body, self._device_decrypt_iv
            )
            self._device_decrypt_iv = next_iv
            sequence = self._sequence(plaintext, self._device_receive_sequence)
            return plaintext, sequence

    def commit_device(self, sequence: int) -> None:
        with self._lock:
            if sequence <= self._device_receive_sequence:
                raise AuthenticationError("replay rejected")
            self._device_receive_sequence = sequence

    def _seal(
        self, keys: _DirectionalKeys, plaintext: bytes, iv: bytes
    ) -> tuple[bytes, bytes]:
        if not isinstance(plaintext, bytes) or not plaintext:
            raise TypeError("plaintext must be bytes")
        padded = plaintext + b"\0" * (-len(plaintext) % AES.block_size)
        ciphertext = AES.new(keys.encryption, AES.MODE_CBC, iv).encrypt(padded)
        signature = hmac.new(keys.signing, plaintext, hashlib.sha256).digest()
        envelope = json.dumps(
            {
                "enc": base64.b64encode(ciphertext).decode("ascii"),
                "sign": base64.b64encode(signature).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode("ascii")
        if len(envelope) > self._max_body:
            raise AuthenticationError("body too large")
        return envelope, ciphertext[-AES.block_size :]

    def _open(
        self,
        keys: _DirectionalKeys,
        body: bytes,
        iv: bytes,
    ) -> tuple[bytes, bytes]:
        if not isinstance(body, bytes) or len(body) > self._max_body:
            raise AuthenticationError("body too large")
        try:
            envelope = json.loads(body)
            if not isinstance(envelope, dict) or set(envelope) != {"enc", "sign"}:
                raise TypeError
            encoded_data = envelope["enc"]
            encoded_signature = envelope["sign"]
            if not isinstance(encoded_data, str) or not isinstance(encoded_signature, str):
                raise TypeError
            ciphertext = base64.b64decode(encoded_data, validate=True)
            signature = base64.b64decode(encoded_signature, validate=True)
            if not ciphertext or len(ciphertext) % AES.block_size or len(signature) != 32:
                raise ValueError
        except AuthenticationError:
            raise
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            binascii.Error,
        ):
            raise AuthenticationError("malformed authenticated payload") from None

        padded = AES.new(keys.encryption, AES.MODE_CBC, iv).decrypt(ciphertext)
        plaintext = padded.rstrip(b"\0")
        if not plaintext:
            raise AuthenticationError("authentication failed")
        expected = hmac.new(keys.signing, plaintext, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            raise AuthenticationError("authentication failed")
        return plaintext, ciphertext[-AES.block_size :]

    @staticmethod
    def _sequence(plaintext: bytes, previous_sequence: int) -> int:
        try:
            payload = json.loads(plaintext)
            sequence = payload["seq_no"]
            if type(sequence) is not int or sequence <= previous_sequence or sequence >= 2**63:
                raise AuthenticationError("replay rejected")
        except AuthenticationError:
            raise
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise AuthenticationError("malformed authenticated payload") from None
        return sequence


@dataclass(slots=True)
class _Session:
    codec: SessionCodec
    source_address: str


class AylaCallbackProtocol:
    """Bind authenticated key exchange and messages to the per-device router."""

    _TOKEN_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

    def __init__(
        self,
        router: AylaLanRouter,
        *,
        random_bytes: Callable[[int], bytes],
        time_value: Callable[[], int],
        max_body: int = 65536,
    ) -> None:
        self._router = router
        self._random_bytes = random_bytes
        self._time_value = time_value
        self._max_body = max_body
        self._sessions: dict[str, _Session] = {}
        self._key_locks: dict[str, RLock] = {}
        self._lock = RLock()

    def key_exchange(
        self,
        *,
        key_id: str,
        source_address: str,
        device_random: str,
        device_time: int,
        now: float,
    ) -> dict[str, object]:
        device = self._router.match_device(key_id=key_id, source_address=source_address)
        controller_random = self._random_token(16)
        controller_time = self._time_value()
        codec = SessionCodec.from_exchange(
            device.config.lan_key,
            device_random=device_random,
            device_time=device_time,
            controller_random=controller_random,
            controller_time=controller_time,
            max_body=self._max_body,
        )
        with self._key_lock(key_id):
            self._router.establish_session(
                key_id=key_id, source_address=source_address, now=now
            )
            with self._lock:
                self._sessions[key_id] = _Session(
                    codec=codec, source_address=source_address
                )
        return {"random_2": controller_random, "time_2": controller_time}

    def _random_token(self, length: int) -> str:
        raw = self._random_bytes(length)
        if not isinstance(raw, bytes):
            raise AuthenticationError("invalid key exchange")
        try:
            injected = raw.decode("ascii")
        except UnicodeError:
            injected = ""
        if len(injected) == length and injected.isalnum():
            return injected

        values = bytearray(raw)
        token: list[str] = []
        for _attempt in range(64):
            for value in values:
                if value < 248:
                    token.append(self._TOKEN_ALPHABET[value % len(self._TOKEN_ALPHABET)])
                    if len(token) == length:
                        return "".join(token)
            values = bytearray(self._random_bytes(length))
        raise AuthenticationError("invalid key exchange")

    def fetch_commands(self, *, key_id: str, source_address: str, now: float) -> bytes:
        with self._key_lock(key_id):
            session = self._session(key_id, source_address)
            payload = self._router.fetch_commands(
                key_id=key_id, source_address=source_address, now=now
            )
            plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            body = session.codec.seal_controller(plaintext)
            _LOGGER.info("Ayla LAN diagnostic stage=COMMAND_FETCH reason=ACCEPTED")
            return body

    def receive_datapoint(
        self,
        *,
        key_id: str,
        source_address: str,
        body: bytes,
        now: float,
    ) -> None:
        if len(body) > self._max_body:
            raise AuthenticationError("body too large")
        with self._key_lock(key_id):
            session = self._session(key_id, source_address)
            plaintext, sequence = session.codec.verify_device(body)
            values = self._parse_datapoints(plaintext)
            self._router.receive_authenticated_datapoints(
                key_id=key_id,
                source_address=source_address,
                values=values,
                now=now,
            )
            session.codec.commit_device(sequence)
            _LOGGER.info(
                "Ayla LAN diagnostic stage=AUTHENTICATED_DATAPOINT reason=ACCEPTED"
            )

    def invalidate(self, key_id: str) -> None:
        with self._key_lock(key_id), self._lock:
            self._sessions.pop(key_id, None)

    def key_id_for_source(self, source_address: str) -> str:
        with self._lock:
            candidates = [
                key_id
                for key_id, session in self._sessions.items()
                if session.source_address == source_address
            ]
        matches = []
        for key_id in candidates:
            try:
                self._router.match_device(
                    key_id=key_id, source_address=source_address
                )
            except RoutingError:
                continue
            matches.append(key_id)
        if len(matches) != 1:
            _LOGGER.warning("Ayla LAN diagnostic stage=SOURCE_ROUTING reason=REJECTED")
            raise RoutingError("callback source has no unique authenticated session")
        return matches[0]

    def _session(self, key_id: str, source_address: str) -> _Session:
        with self._lock:
            session = self._sessions.get(key_id)
            if session is None or session.source_address != source_address:
                raise RoutingError("no authenticated session for callback")
            return session

    def _key_lock(self, key_id: str) -> RLock:
        with self._lock:
            return self._key_locks.setdefault(key_id, RLock())

    @staticmethod
    def _parse_datapoints(plaintext: bytes) -> dict[str, bool]:
        try:
            payload = json.loads(plaintext)
            data = payload["data"]
            if not isinstance(data, dict) or set(data) != {"name", "value"}:
                raise ValueError
            name = data["name"]
            value = data["value"]
            if not isinstance(name, str) or not name or type(value) not in (bool, int):
                raise ValueError
            if value not in (0, 1, False, True):
                raise ValueError
            return {name: bool(value)}
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise ProtocolError("malformed datapoint response") from None
