# Clean-room Ayla LAN protocol contract

This contract records observed behavior and public protocol characteristics needed to implement the APC PH6U4X32 integration. It is a behavioral specification, not copied source.

## Scope and identities

- Durable device identity: Ayla device serial number (DSN). A DHCP address is transient metadata, never node identity.
- Expected discovered Boolean controls: LED, Outlet 1, Outlet 2, Outlet 3, USB Charger 1, and USB Charger 2. Property names come from bootstrap metadata; do not infer identity from array order.
- Child IoX addresses are deterministic hashes of `(DSN, property name)` and remain at most 14 alphanumeric characters.

## Bootstrap boundary

Already-provisioned devices require these protected fields:

- DSN and friendly product name;
- current/last-known LAN address;
- `lanip_key_id` and `lanip_key`;
- discovered property name, display name, base type, direction, and writability.

These fields are secret-bearing or identifying and must be redacted from logs. APC account credentials and cloud access tokens are bootstrap-only and must not be stored in the plugin's normal configuration.

## LAN registration and callback flow

1. The controller runs a LAN-reachable HTTP callback server, default port `10275`.
2. It registers with `POST http://<device>/local_reg.json`, sending a local registration object containing callback IP, port, URI `/local_lan`, and a `notify` flag. The observed device success response is HTTP `202 Accepted`; no JSON success object is required.
3. `notify=0` renews registration. `notify=1` indicates queued command data.
4. The device initiates `POST /local_lan/key_exchange.json` to the controller with a nested `key_exchange` object containing `ver=1`, `proto=1`, `key_id`, a 16-character `random_1`, and microsecond epoch `time_1`.
5. The controller matches key ID to exactly one configured device, generates a 16-character `random_2` and microsecond epoch `time_2`, derives directional signing/encryption material, and returns top-level `random_2` and `time_2` fields.
6. The device retrieves queued work with `GET /local_lan/commands.json`.
7. The device sends property telemetry to `POST /local_lan/property/datapoint.json` as encrypted and signed content.

All callback requests must be matched to the expected device by key ID and/or current source address. A request must never fall back to “first configured device.”

## Command and state contract

For each property the runtime tracks:

- `actual`: last verified device value or unknown;
- `desired`: latest requested value or none;
- deadline, attempt count, and last-send time;
- last confirmed device update time.

`DON`/`DOF` behavior:

1. Replace any older pending intent for the same property with the newest request.
2. Queue the property write and send a bounded registration notify.
3. When the device fetches commands, return the command but retain it as pending.
4. Do not modify IoX `ST` from the command request or command-fetch event.
5. Only a verified datapoint callback or verified readback updates `actual` and IoX `ST`.
6. A matching report clears the pending command. A contradictory report updates truth but leaves the command pending until retry exhaustion or deadline.
7. Timeout/retry exhaustion clears pending status, retains the last verified `ST`, marks communication stale/failed, and emits a redacted notice.

## Availability and timing defaults

- Outbound HTTP connect/read timeout: bounded and configurable; initial proof-of-concept target is no more than 3 seconds per attempt.
- Command confirmation timeout: default 8 seconds.
- Maximum command attempts: default 3.
- Retry uses capped backoff and is per device; one unreachable strip cannot block callbacks or commands for another.
- Registration keepalive: short poll supervision; full reconciliation and address refresh: long poll.
- Plugin online, strip reachable, command pending, and state stale are distinct signals.

## DHCP recovery

On timeout or connection refusal:

1. Preserve the DSN and last verified state.
2. Mark connectivity false/stale.
3. Attempt local address rediscovery or refresh of bootstrap metadata without changing node addresses.
4. Re-register and repeat key exchange at the new address.
5. Resume commands only after the session belongs to the expected DSN/key ID.

A literal address may be accepted as a temporary hint, not permanent identity.

## Cryptographic requirements

- Treat the LAN key as its UTF-8 wire string. For each direction concatenate the ordered random pair, decimal time pair, and discriminator (`0` signing, `1` AES, `2` IV), then apply the observed double-HMAC-SHA256 construction. Reverse both randoms and times for device-to-controller material.
- Send only base64 `enc` and `sign` fields. Sign the unpadded plaintext with HMAC-SHA256, zero-pad only to the next AES block boundary (no extra block when aligned), and maintain continuous CBC state independently in each direction.
- Verify HMAC before accepting any decrypted datapoint.
- Reject invalid base64, invalid block length, unknown key IDs, replay/out-of-sequence traffic where observable, and signature mismatch.
- Never log raw keys, derived keys, random/time tuples together, encrypted payloads containing user metadata, or decrypted sensitive payloads.

## HTTP behavior

- Use a threaded callback server or equivalent bounded concurrency.
- Set explicit body limits and content-length validation.
- Return 4xx for malformed/unknown requests and do not mutate session state.
- Command fetch is per-device and lock-protected.
- Datapoint handling verifies, parses, maps by property name, confirms the queue, and invokes the PG3 reporting callback.

## Testable assumptions and unknowns

1. **Sequence enforcement:** a monotonically increasing command sequence is observed; device rejection/replay behavior is not yet proven.
2. **Readback mechanism:** unsolicited datapoints are observed. A complete all-property read request is not yet proven; query may need re-registration/notify plus cached-state freshness rules.
3. **Property names:** must be imported from the user's device metadata; fixed ordering is prohibited.
4. **IP rediscovery:** mDNS/broadcast capability is unknown. The fallback is a protected metadata refresh, not permanent reliance on a stale IP.

Each assumption must be resolved with sanitized fixtures or an explicitly approved noncritical hardware test before local-store acceptance.

The wire-format tests use synthetic known-answer values independently derived from the public Ayla protocol description and implementation at upstream commit `513c71157d5c03b89bab04b3ad3b6c2de8fd20b9`. Production code is independently authored and contains no copied upstream source.
