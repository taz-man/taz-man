# APC PH6U4X32 PG3x Plugin

Native eisy/PG3x integration for already-provisioned APC PH6U4X32/PH6U4X32W smart strips using the local Ayla LAN protocol.

## Safety and scope

- State is confirmation-based: `DON`/`DOF` intent never changes IoX `ST` until verified device telemetry arrives.
- Home Assistant and an external MQTT broker are not runtime dependencies.
- Initial Wi-Fi provisioning and acquisition of Ayla LAN material are separate prerequisites.
- Do not place APC account credentials, access tokens, LAN keys, or device configuration in Git.
- The implementation is clean-room and does not copy the unlicensed APCBridge/AylaLocalAPI source.

## Node mapping

One configured strip creates one controller and exactly six children. Child
addresses are deterministic hashes of the DSN and discovered property name, so
they remain stable across plugin restarts, device restarts, and DHCP changes.

| Role | IoX node |
| --- | --- |
| Strip status | Controller |
| LED | LED child |
| Outlets | Outlet 1, Outlet 2, and Outlet 3 children |
| USB controls | USB 1 and USB 2 children |

Property identity comes from protected bootstrap metadata and is mapped by role,
not list order. Startup rejects incomplete, duplicate, non-Boolean, read-only, or
extra endpoint mappings.

## Status meanings

- `ST` on a child is the last authenticated device report. A command never
  changes it optimistically.
- `Connected` becomes true only after authenticated, confirmed device telemetry
  has been received and is retained until a transport failure. Registration, key
  exchange, command fetch, and keepalive traffic do not mark the strip connected.
- `Command Pending` means an On/Off intent is awaiting matching telemetry.
- `State Stale` means no recent verified report was received or communication
  failed. The last confirmed `ST` remains visible and must not be mistaken for a
  fresh reading. `Connected` can therefore be true while `State Stale` is true
  after telemetry ages out without a transport failure.
- Controller `Last Confirmed Update Age` is seconds since the last authenticated,
  confirmed telemetry report. Zero before the first report does not mean the
  state is fresh; consult `State Stale`.

## Development

```bash
uv sync --extra dev
uv run pytest -q
```

## Hardware acceptance gate

This package has automated/simulated validation only; it does not claim hardware
validation. Installing it, retrieving live LAN material, or issuing a real LED,
outlet, or USB command requires explicit human approval and a named noncritical
test load. Never test with medical, safety, refrigeration, networking, server, or
other critical loads. Production-store publication also requires separate human
approval. The existing Home Assistant APCBridge remains untouched until the
native plugin has passed an approved isolated hardware exercise.

## Troubleshooting

- Configuration notice: use its bounded reason code with the table in
  `POLYGLOT_CONFIG.md`. Confirm `callback_host` is a plain IP address or hostname
  visible to the strip. For the default path, startup accepts either installed
  `data/apc-bootstrap.json` or PG3x-uploaded top-level `apc-bootstrap.json`, but
  rejects the configuration if both exist. An explicit non-default path is used
  exactly as configured. Bootstrap files must be regular, owned by the plugin
  process account, single-linked, no larger than 64 KiB, and mode `0600`. On
  POSIX, startup may normalize mode only for the exact PG3x-uploaded top-level
  default candidate after descriptor-based checks and before reading it. It
  never repairs an installed-default or explicit/custom path. PG3x extraction
  can still expose the upload briefly before startup performs this hardening;
  see `POLYGLOT_CONFIG.md` for the limitation and safe workflow.
- Connected is false: verify LAN reachability and callback port 10275. Do not
  expose or forward the callback port to the Internet.
- State Stale or Command Pending persists: the runtime clears commands after the
  configured bounded deadline/retries; check address metadata and reconnect with
  Discover. It never fabricates success.
- DHCP change: update only the protected bootstrap `address`; the next failed
  registration refreshes it by DSN/key ID while preserving node addresses.
- Diagnostics intentionally contain only bounded reason codes and omit paths,
  callback hosts, DSN/address/key material, property identifiers, payloads, and
  dynamic exception text. Do not paste the bootstrap file into logs or support
  requests.
