# Configuration

The plugin expects a protected bootstrap file at `data/apc-bootstrap.json`. It
contains the already-provisioned strip's stable identity, current LAN address,
six discovered Boolean property mappings, and Ayla LAN key material. Set file
mode `0600`; startup rejects a symlink, non-regular file, or broader permissions
on eisy. The containing `data` directory is installed as `0700`.

Do not commit that file. Account credentials are not intended to remain configured after bootstrap.

Parameters:

- `bootstrap_config_path`: path under the plugin directory to protected bootstrap data.
- `callback_host`: eisy LAN address reachable from the strip. This is required.
- `callback_port`: LAN-reachable HTTP callback port advertised to the strip; default `10275`.
- `command_timeout_seconds`: maximum wait for matching device telemetry before a command fails.
- `max_command_attempts`: bounded resend count before failure.

The eisy and strip must be on mutually reachable LAN paths. No public exposure or router port forwarding is required.

The file format is:

```json
{
  "dsn": "device serial",
  "product_name": "APC PH6U4X32",
  "address": "current LAN address",
  "lanip_key_id": "Ayla LAN key id",
  "lanip_key": "base64 Ayla LAN key",
  "properties": [
    {"role": "led", "name": "discovered name", "label": "LED", "base_type": "boolean", "writable": true}
  ]
}
```

Provide exactly one entry for each role: `led`, `outlet_1`, `outlet_2`,
`outlet_3`, `usb_1`, and `usb_2`. Order is irrelevant. Never enter account
credentials or cloud tokens. PG3x custom parameters persist only the protected
file path and non-secret timing/network settings; secret material remains in the
owner-only file. Runtime restart data contains only DSN, last address, and role
names. Logs and notices redact all identities, addresses, keys, payloads, and
dynamic exception text.

On/Off commands remain pending until authenticated telemetry confirms them.
Timeout/retry failure retains the last confirmed state and marks it stale. An
actual installation or control exercise requires explicit human approval of a
named noncritical load; hardware validation and production-store publication are
not implied by the automated test suite.
