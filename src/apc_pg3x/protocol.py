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
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from .device import AylaLanRouter, RoutingError
from .transport import ProtocolError


class AuthenticationError(ProtocolError):
    """An authenticated callback envelope was invalid or replayed."""


@dataclass(frozen=True, slots=True)
class _DirectionalKeys:
    encryption: bytes
    signing: bytes
    iv_seed: bytes


class SessionCodec:
    """Encode and authenticate messages in both session directions."""

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
        self._controller_send_sequence = 0
        self._controller_receive_sequence = 0
        self._device_receive_sequence = 0

    @classmethod
    def from_exchange(
        cls,
        shared_key: str,
        *,
        device_random: bytes,
        device_time: int,
        controller_random: bytes,
        controller_time: int,
        max_body: int = 65536,
    ) -> SessionCodec:
        try:
            key = base64.b64decode(shared_key, validate=True)
        except (binascii.Error, ValueError):
            raise AuthenticationError("invalid session key") from None
        if len(key) < 16:
            raise AuthenticationError("invalid session key")
        if (
            not device_random
            or not controller_random
            or len(device_random) >= 2**16
            or len(controller_random) >= 2**16
        ):
            raise AuthenticationError("invalid key exchange")
        if not 0 <= device_time < 2**64 or not 0 <= controller_time < 2**64:
            raise AuthenticationError("invalid key exchange")
        transcript = (
            len(device_random).to_bytes(2, "big")
            + device_random
            + device_time.to_bytes(8, "big")
            + len(controller_random).to_bytes(2, "big")
            + controller_random
            + controller_time.to_bytes(8, "big")
        )
        master = hmac.new(key, transcript, hashlib.sha256).digest()
        return cls(
            controller_keys=cls._derive_direction(master, b"controller-to-device"),
            device_keys=cls._derive_direction(master, b"device-to-controller"),
            max_body=max_body,
        )

    @staticmethod
    def _derive_direction(master: bytes, label: bytes) -> _DirectionalKeys:
        return _DirectionalKeys(
            encryption=hmac.new(master, b"encrypt:" + label, hashlib.sha256).digest()[:16],
            signing=hmac.new(master, b"sign:" + label, hashlib.sha256).digest(),
            iv_seed=hmac.new(master, b"iv:" + label, hashlib.sha256).digest(),
        )

    def seal_controller(self, plaintext: bytes) -> bytes:
        with self._lock:
            self._controller_send_sequence += 1
            return self._seal(self._controller_keys, plaintext, self._controller_send_sequence)

    def seal_device(self, plaintext: bytes, *, sequence: int) -> bytes:
        with self._lock:
            return self._seal(self._device_keys, plaintext, sequence)

    def open_controller(self, body: bytes) -> bytes:
        with self._lock:
            plaintext, sequence = self._open(
                self._controller_keys, body, self._controller_receive_sequence
            )
            self._controller_receive_sequence = sequence
            return plaintext

    def open_device(self, body: bytes) -> bytes:
        plaintext, sequence = self.verify_device(body)
        self.commit_device(sequence)
        return plaintext

    def verify_device(self, body: bytes) -> tuple[bytes, int]:
        with self._lock:
            return self._open(
                self._device_keys, body, self._device_receive_sequence
            )

    def commit_device(self, sequence: int) -> None:
        with self._lock:
            if sequence <= self._device_receive_sequence:
                raise AuthenticationError("replay rejected")
            self._device_receive_sequence = sequence

    def _seal(self, keys: _DirectionalKeys, plaintext: bytes, sequence: int) -> bytes:
        if sequence < 1:
            raise ValueError("sequence must be positive")
        if not isinstance(plaintext, bytes):
            raise TypeError("plaintext must be bytes")
        iv = self._iv(keys, sequence)
        ciphertext = AES.new(keys.encryption, AES.MODE_CBC, iv).encrypt(pad(plaintext, AES.block_size))
        signature = hmac.new(keys.signing, plaintext, hashlib.sha256).digest()
        envelope = json.dumps(
            {
                "seq_no": sequence,
                "data": base64.b64encode(ciphertext).decode("ascii"),
                "signature": base64.b64encode(signature).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode("ascii")
        if len(envelope) > self._max_body:
            raise AuthenticationError("body too large")
        return envelope

    def _open(
        self, keys: _DirectionalKeys, body: bytes, previous_sequence: int
    ) -> tuple[bytes, int]:
        if not isinstance(body, bytes) or len(body) > self._max_body:
            raise AuthenticationError("body too large")
        try:
            envelope = json.loads(body)
            sequence = envelope["seq_no"]
            encoded_data = envelope["data"]
            encoded_signature = envelope["signature"]
            if (
                type(sequence) is not int
                or sequence <= previous_sequence
                or sequence >= 2**64
            ):
                raise AuthenticationError("replay rejected")
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

        try:
            iv = self._iv(keys, sequence)
            plaintext = unpad(
                AES.new(keys.encryption, AES.MODE_CBC, iv).decrypt(ciphertext), AES.block_size
            )
        except ValueError:
            raise AuthenticationError("authentication failed") from None
        expected = hmac.new(keys.signing, plaintext, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            raise AuthenticationError("authentication failed")
        return plaintext, sequence

    @staticmethod
    def _iv(keys: _DirectionalKeys, sequence: int) -> bytes:
        return hmac.new(keys.iv_seed, sequence.to_bytes(8, "big"), hashlib.sha256).digest()[:16]


@dataclass(slots=True)
class _Session:
    codec: SessionCodec
    source_address: str


class AylaCallbackProtocol:
    """Bind authenticated key exchange and messages to the per-device router."""

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
        device_random: bytes,
        device_time: int,
        now: float,
    ) -> dict[str, object]:
        device = self._router.match_device(key_id=key_id, source_address=source_address)
        controller_random = self._random_bytes(16)
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
        return {"random": controller_random, "time": controller_time}

    def fetch_commands(self, *, key_id: str, source_address: str, now: float) -> bytes:
        with self._key_lock(key_id):
            session = self._session(key_id, source_address)
            payload = self._router.fetch_commands(
                key_id=key_id, source_address=source_address, now=now
            )
            plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            return session.codec.seal_controller(plaintext)

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
            properties = payload["properties"]
            if not isinstance(properties, list) or not properties:
                raise ValueError
            values: dict[str, bool] = {}
            for entry in properties:
                prop = entry["property"]
                name = prop["name"]
                value = prop["value"]
                if not isinstance(name, str) or not name or type(value) is not bool or name in values:
                    raise ValueError
                values[name] = value
            return values
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise ProtocolError("malformed datapoint response") from None
