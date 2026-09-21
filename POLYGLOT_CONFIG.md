# Configuration

The plugin reads bootstrap material only from the canonical persistent path
`data/apc-bootstrap.json`. It contains the already-provisioned strip's stable
identity, current LAN address, six discovered Boolean property mappings, and
Ayla LAN key material. Configure the Local Store **Persistent Folder** as
`data`. Do not use **Plugin Custom Data** (`nsdata`/`CUSTOMNS`) for this
per-install secret, and do not enter a bootstrap path as a custom parameter.
Set the Local Store purchase-option version to `0.1.2`; a same-version reinstall
does not distinguish this corrected runtime from the prior `0.1.1` build.

## PG3x ZIP upload layout

Create a ZIP containing exactly one regular member named `apc-bootstrap.json`
and upload it through the installed plugin's PG3x **ZIP File Upload** control.
PG3x 3.4.24 says it extracts that member into the plugin's `data` directory,
producing exactly `data/apc-bootstrap.json`. A top-level plugin-root fallback,
an explicit alternate path, symlinks, hard links, traversal, and recursive file
searches are prohibited.

PG3x does not document the extracted owner or mode. On POSIX, and only for the
exact canonical candidate, startup opens every path component without following
symlinks and validates root containment, owner, regular-file type, single-link
status, and the 64 KiB size limit before changing mode through the verified
descriptor. It fsyncs and re-stats that same descriptor, requires unchanged
device/inode identity and exact mode `0600`, and only then reads bounded bytes.
Unsupported operations or any failed/replaced invariant fail closed with a
bounded reason code.

This cannot remove the short exposure between PG3x extracting the upload and
plugin startup hardening it. Upload only through the supported local PG3x UI and
restart only this plugin immediately afterward. Require runtime acceptance and
passive topology read-back; upload completion alone is not acceptance.

The current runtime needs the source on restart and when refreshing address
metadata, so it is retained only at verified mode `0600` in persistent `data`.
It is not consumed or unlinked. If a future design imports it into another
protected cache, unlinking the source may be attempted only as best-effort:
PG3x staging-copy deletion and whether backup/restore or reinstall can resurrect
the uploaded file are not guaranteed. Neither retention nor unlink is secure
erasure, and this plugin makes no secure erasure claim.

Do not commit that file. Account credentials are not intended to remain configured after bootstrap.

Parameters:

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
credentials or cloud tokens. PG3x custom parameters persist only non-secret
timing/network settings; the canonical bootstrap path is fixed in code, and
secret material remains in the owner-only file. Runtime restart data contains
only DSN, last address, and role names. Logs and notices redact all identities,
addresses, keys, payloads, and dynamic exception text.

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
- `BOOTSTRAP_TOP_LEVEL`: the decoded JSON top level is not an object.
- `BOOTSTRAP_REQUIRED_SECTIONS`: the object has none of the required bootstrap
  sections.
- `BOOTSTRAP_IDENTITY`: the device identity fields are absent or invalid.
- `BOOTSTRAP_LAN_FIELDS`: the LAN address or key fields are absent or invalid.
- `BOOTSTRAP_PROPERTY_COLLECTION`: the property collection is not a list of
  exactly six entries.
- `BOOTSTRAP_PROPERTY_ENTRY`: a property entry is not an object.
- `BOOTSTRAP_PROPERTY_FIELDS`: a property's required role, name, or label is
  absent or invalid.
- `BOOTSTRAP_PROPERTY_ROLE`: the six roles do not match the supported controls.
- `BOOTSTRAP_PROPERTY_TYPE`: a property is not Boolean.
- `BOOTSTRAP_PROPERTY_WRITABLE`: a property is not writable.
- `BOOTSTRAP_DUPLICATE_ROLE`: two or more property entries use the same role.
- `BOOTSTRAP_DUPLICATE_NAME`: two or more property entries use the same device
  property name.
- `BOOTSTRAP_PATH`: a noncanonical configured path, root escape, or symlinked
  parent was rejected.
- `CALLBACK_HOST`: the callback host is empty or is not a plain IP address or DNS
  hostname.
- `CALLBACK_PORT`: the callback port is outside 1-65535.
- `SETTINGS`: another bounded numeric/runtime setting is invalid.
- `STARTUP`: a non-configuration operating-system startup failure occurred.
