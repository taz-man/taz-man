# PG3x bootstrap mechanism decision

Date: 2026-09-20
Scope: APCSurge private Local Store plugin on PG3x 3.4.24
Status: accepted with documented PG3x lifecycle uncertainty; implementation candidate requires independent review

## Decision

Use the supported per-install **ZIP File Upload** into the plugin's canonical `data/` directory, with the Local Store **Persistent Folder** set to `data`. Do not use **Plugin Custom Data** (`nsdata` delivered as `CUSTOMNS`/`NSCustom`) for the APC/Ayla bootstrap.

The upload mechanism is the only compared mechanism whose scope matches the requirement: one secret-bearing bootstrap for one installed slot. PG3x 3.4.24 explicitly says that the ZIP is extracted to that installed plugin's `data` directory, and a Universal Devices employee states that this directory is slot-local and included in PG3 backup. The plugin must retain the existing fail-closed, pre-read hardening for the one canonical uploaded file because PG3x does not document or preserve an owner-only extracted mode.

The user explicitly accepted the remaining undocumented PG3x staging,
deletion, backup/restore, and reinstall behavior. The implementation may proceed
without claiming guarantees in those areas; its immutable result still requires
the pre-created independent code/artifact review before deployment.

## Evidence and comparison

| Question | Plugin Custom Data (`nsdata` / `CUSTOMNS`) | Canonical `data/` upload |
|---|---|---|
| Supported purpose and location | Official publishing docs call this developer-supplied “Text data” and explicitly give a secret API key as an example. Official `udi_interface` documentation says node-server-specific data is stored in the Polyglot database and sent during configuration. The PG3x developer form stores it as the Local Store record's `nsdata` field. | The PG3x 3.4.24 details UI says: “This will upload the zip file and extract it to the `data` directory of the plugin.” A UDI employee identifies the normal path as `/var/polyglot/pg3/ns/<slot>/data`; developer-mode storage is instead under the development path. |
| Scope | Store-entry/developer supplied. The publishing docs say it is sent at install and may be changed by the developer at any time, then pushed periodically. Therefore every installation sourced from that store entry receives the same value; it is not a user-entered or per-install secret channel. | Per installed plugin slot: the upload request is made from that installed plugin's details page using its UUID and profile number, and extraction targets that instance's `data` directory. |
| Confidentiality and exposure | Not acceptable for this bootstrap. The developer UI exposes the value as editable text. More importantly, current official `udi_interface` source logs the complete inbound item at debug (`interface.py` `_handleInput`) and separately logs the custom key and value before publishing `CUSTOMNS`. The docs' “secret key” example therefore does not establish log secrecy. Store/database administrators can also access the source value. | Routine UI accepts a ZIP but provides no download control; the UDI employee states uploaded files cannot be downloaded through the UI, while appliance administrators can access them through SCP. The PG3x frontend logs selected file metadata/name, not file contents. Neither docs nor UI claim encryption at rest, so this protects against routine UI disclosure, not against the appliance administrator/root. Plugin logs must never include contents, paths, or dynamic parse exceptions. |
| Size and format | Documented only as “Text data.” No public maximum, encoding contract beyond text, or structured format limit was found. | PG3x 3.4.24's file chooser accepts `.zip`; UI/help says it extracts the ZIP to `data`. No public compressed/uncompressed size, member-count, or path-policy limit was found. The APC plugin must therefore keep its own 64-KiB single-file bound and exact schema checks; that is a plugin limit, not a claimed PG3x limit. |
| Delivery/update/restart | Official docs: sent at install, delivered on plugin start through `NSCustom`, and pushed periodically so a developer can update it at any time. `udi_interface` also publishes `CUSTOMNS` during initial configuration and when Polyglot sends a changed custom item. This behavior is unsuitable for immutable per-install bootstrap material because a store edit can replace all installs' value independently of a release. | Upload is an explicit per-install operator action. The 3.4.24 frontend posts the ZIP and reports completion, but exposes no content-change event to the plugin. Treat upload completion as extraction only: restart only the plugin, then require runtime acceptance and passive topology read-back. |
| Reinstall and backup | Database persistence is documented generally by `udi_interface`, but the publishing docs do not establish per-install ownership, backup inclusion, or deletion semantics for the developer `nsdata` source. Reinstall would source the store value again. | Publishing docs define **Persistent Folder** as a plugin-home folder that survives reinstall; configure it as `data`. The UDI employee explicitly states uploaded `data` files are included in PG3 backup. |
| Owner/mode | Filesystem ownership/mode is not applicable. Transport and database/log exposure remain. | Public docs and 3.4.24 UI state no owner/mode guarantee. The prior supported-UI diagnostic upload was rejected as `BOOTSTRAP_MODE`, proving extraction did not preserve the required owner-only mode. Continue only with the already-reviewed design that opens the exact default candidate without following links, verifies plugin-euid ownership/regular file/link count/size and parent traversal before reading, performs descriptor-based `0600` normalization, fsyncs and re-verifies identity/mode, and fails closed otherwise. This is compensating runtime hardening, not a PG3x guarantee. |
| Clear/scrub through supported means | `udi_interface.Custom.clear()` can save an empty custom object, but no official document establishes that a plugin can clear the developer/store `nsdata` source. A later periodic store push can repopulate it. It is therefore not a dependable one-time consume-and-scrub channel. | No PG3x UI delete/download control was found. The current runtime needs the canonical source for restart and address refresh, so it retains that source only after verifying mode `0600`. A future protected-cache import may attempt unlink only as best-effort; no source establishes that PG3x removes staging copies or that backup/restore or reinstall cannot resurrect it. Neither approach is secure erasure. |
| Distinct deployed version | Custom Data can change periodically without changing plugin version, so its delivery cannot prove which code accepted it. | Official publishing docs require a version in each purchase option and identify the installation URL/branch. PG3x details distinguishes installed/current and latest versions, but does not expose a Git commit hash. Every deployable code change therefore needs a new semver reported consistently by the purchase option and runtime; verify the supported UI's installed/current version after reinstall and independently verify the public repository head/artifact hash before installation. Do not use an unchanged `0.1.0` label as evidence of a distinct deployed build. |

## Why the earlier top-level fallback was wrong

The supported PG3x UI and UDI employee statement both define the upload target as the plugin's canonical `data/` directory. There is no documented second extraction target at the plugin top level. Searching both `data/<name>` and `<plugin-root>/<name>` treated an unsupported guess as an alternate bootstrap source, created ambiguity, and expanded the secret-search surface. Runtime discovery must use exactly one deterministic candidate under `data/`; explicit operator paths, top-level fallbacks, recursive searches, and “first file found” behavior remain prohibited.

## Remaining documented limits (not blockers to this choice)

PG3x's public material does not publish upload size/member limits, atomic replacement behavior, extraction owner/mode, or a UI delete operation. The implementation must not invent those guarantees. It must enforce its own strict one-file/schema/size/path invariants, tolerate replacement only through a verified descriptor, and report bounded reason codes. If independent review concludes that plugin-side deletion of an accepted file is not a supported operation, the smallest UDI question is:

> On PG3x 3.4.24, may an installed plugin delete a file uploaded into its configured persistent `data` folder after securely importing it, and will that deletion be preserved across PG3 backup/restore and reinstall?

## Sources

- UD Developer Docs, “Publishing”: https://developer.isy.io/docs/pkgpublish/publish
  - Local Store is maintained only in the local PG3(x) database.
  - Plugin Custom Data is text sent at install/start through `NSCustom`, can contain a secret key, and is pushed periodically for developer updates.
  - Persistent Folder survives reinstall.
  - Purchase options carry required versions and installation URL/optional branch.
- UD Developer Docs, Python interface API: https://developer.isy.io/docs/API/pg/
  - `CUSTOMNS` is the plugin-specific-data event and data events are emitted initially/when PG3 data changes.
- Official `UniversalDevicesInc/udi_python_interface`, commit `21f8bdd96033b1fac5e603ddce662a539aa441f4` inspected read-only:
  - https://github.com/UniversalDevicesInc/udi_python_interface/blob/21f8bdd96033b1fac5e603ddce662a539aa441f4/Flow.md
  - https://github.com/UniversalDevicesInc/udi_python_interface/blob/21f8bdd96033b1fac5e603ddce662a539aa441f4/udi_interface/interface.py
  - https://github.com/UniversalDevicesInc/udi_python_interface/blob/21f8bdd96033b1fac5e603ddce662a539aa441f4/udi_interface/custom.py
- PG3x 3.4.24 installed frontend/help, read-only at `https://eisy.local:3000/`:
  - package metadata identifies frontend version 3.4.24;
  - installed-plugin details labels the feature “ZIP File Upload,” accepts `.zip`, and says extraction is to the plugin `data` directory;
  - upload is scoped by installed plugin UUID/profile number;
  - developer form field is `nsdata` and the feature flag is `fileUpload`.
- Universal Devices employee statement (bmercier), PG3x v3.2.22 support thread: https://forum.universal-devices.com/topic/43433-support-thread-for-pg3x-v3222-april-16th-2024/
  - uploads are stored in the plugin's slot-local `data` folder, are unavailable for UI download, and are included in PG3 backup.
- Prior exact supported-UI diagnostic evidence (repository history `175742b`, `docs/deployment-diagnostic-retry-evidence.md`): upload completed but runtime rejected the extracted file as `BOOTSTRAP_MODE`; no unsupported filesystem/database access was used.

## Safety accounting

This investigation used documentation, official source, public frontend assets/help, and read-only UI inspection only. Bootstrap material was not decrypted, generated, read, uploaded, or changed. No Local Store record, installed configuration, plugin process, repository publication, APCBridge instance, node, or hardware output was changed.
