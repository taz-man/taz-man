# Configuration

The plugin expects a protected bootstrap file at `data/apc-bootstrap.json`. It
contains the already-provisioned strip's stable identity, current LAN address,
six discovered Boolean property mappings, and Ayla LAN key material. Set file
mode `0600`; startup rejects a symlink, non-regular file, or broader permissions
on eisy. The file must also be owned by the account running the plugin. The
containing `data` directory is installed as `0700`.

## PG3x ZIP upload layout

PG3x's file-upload control extracts ZIP members at the plugin root. For the
default `bootstrap_config_path`, the plugin checks exactly two deterministic
locations: the installed `data/apc-bootstrap.json` path and the uploaded
top-level `apc-bootstrap.json` path. It accepts the one that exists. If both
exist, startup fails with `BOOTSTRAP_AMBIGUOUS`; it never guesses between them.
Paths containing `..`, paths outside the plugin root, and paths whose parent is
a symlink are rejected. A final-component symlink is also rejected by the
protected-file check.

The supported uploader can create the top-level default candidate with broader
permissions than `0600`. On POSIX, and only for that exact fallback candidate,
startup opens the file without following symlinks and validates root
containment, owner, regular-file type, single-link status, and the 64 KiB size
limit before changing mode through the verified descriptor. It flushes and
re-validates that same descriptor before reading any bytes. Installed-default
and explicit/custom paths are never repaired. Unsupported operations or any
failed/replaced invariant fail closed with a bounded reason code.

This cannot remove the short exposure between PG3x extracting the upload and
plugin startup hardening it. The bootstrap must therefore contain only the
dedicated local-device material described below, be uploaded only through the
supported local PG3x UI, and not be left staged while the plugin is stopped.

An explicit non-default `bootstrap_config_path` remains authoritative: no
fallback is attempted, and the configured path must remain beneath the plugin
root. The same owner, regular-file, and `0600` checks apply.

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

## Startup reason codes

Startup notices and logs contain only one bounded code, never a configured path,
host, device identity, address, property name, key identifier, LAN key, token,
derived key, payload, or exception text:

- `BOOTSTRAP_MISSING`: no file exists at the selected location.
- `BOOTSTRAP_TYPE`: the selected object is not a regular file or is a symlink.
- `BOOTSTRAP_OWNER`: the file is not owned by the plugin process account.
- `BOOTSTRAP_MODE`: the file mode is not exactly `0600`.
- `BOOTSTRAP_LINK`: the file has more or fewer than one filesystem link.
- `BOOTSTRAP_SIZE`: the file exceeds the 64 KiB bootstrap limit.
- `BOOTSTRAP_HARDEN`: descriptor-safe open, mode normalization, flush, or
  post-change verification was unavailable or failed.
- `BOOTSTRAP_JSON`: the file cannot be read as UTF-8 JSON.
- `BOOTSTRAP_SCHEMA`: required fields or the exact six writable Boolean mappings
  are invalid.
- `BOOTSTRAP_PATH`: a configured path escapes the plugin root or traverses a
  symlinked parent.
- `BOOTSTRAP_AMBIGUOUS`: both documented default candidates exist.
- `CALLBACK_HOST`: the callback host is empty or is not a plain IP address or DNS
  hostname.
- `CALLBACK_PORT`: the callback port is outside 1-65535.
- `SETTINGS`: another bounded numeric/runtime setting is invalid.
- `STARTUP`: a non-configuration operating-system startup failure occurred.
