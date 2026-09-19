# APC PH6U4X32 PG3x Plugin

Native eisy/PG3x integration for already-provisioned APC PH6U4X32/PH6U4X32W smart strips using the local Ayla LAN protocol.

## Safety and scope

- State is confirmation-based: `DON`/`DOF` intent never changes IoX `ST` until verified device telemetry arrives.
- Home Assistant and an external MQTT broker are not runtime dependencies.
- Initial Wi-Fi provisioning and acquisition of Ayla LAN material are separate prerequisites.
- Do not place APC account credentials, access tokens, LAN keys, or device configuration in Git.
- The implementation is clean-room and does not copy the unlicensed APCBridge/AylaLocalAPI source.

## Current status

The project is in local-store proof-of-concept development. Implemented and tested:

- stable IoX-safe endpoint addresses;
- confirmed-state command semantics;
- per-property command retention until matching device telemetry;
- PG3x server manifest and IoX profile for controller/switch nodes.

Still required before hardware installation:

- encrypted Ayla LAN key exchange and callback server;
- PG3x runtime node classes;
- safe import of existing cached device/LAN metadata;
- reconnect, DHCP recovery, timeout/retry, and hardware acceptance tests.

## Development

```bash
uv sync --extra dev
uv run pytest -q
```

## Hardware acceptance gate

An actual install or outlet command requires explicit approval of a noncritical test endpoint. The existing Home Assistant APCBridge remains untouched until the native plugin has passed isolated tests.
