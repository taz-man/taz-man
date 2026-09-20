# Diagnostic candidate deployment and bootstrap retry evidence

Date: 2026-09-20

## Immutable candidate

- Independent review approved commit `73a64d70c8a15f42649ab6bc2dccde5e5c037aa3` (tree `6d71fbd889a6daf5fd1609657136e07f1eafccd6`).
- Approved source archive SHA-256: `baea67a38404ca6914353fd7508027b0831fed7811b22f88d79f84dbf989a06e`.
- Approved wheel SHA-256: `e7a5c8a2f6a61a0c5b69ffb6696a9185a53285df9fbcf543f4801155428c5c0a`.
- The exact approved commit was published to the already-authorized public repository `main` branch. Anonymous remote-head read-back matched the full commit SHA before Local Store deployment.

## Supported PG3x deployment

- Refreshed the PG3x Local Store and reinstalled APCSurge into existing slot 3 through the supported `eisy.local` UI.
- Post-install slot read-back: APCSurge 0.1.0, Local Store, Connected, zero nodes.
- No direct appliance filesystem or backend manipulation occurred.

## Protected bootstrap retry

- Recovered the bootstrap only from the retained encrypted Home Assistant backup in the protected operator workspace.
- Schema validation confirmed one LAN-enabled APC strip and exactly six writable Boolean mappings: LED, Outlet 1-3, and USB Charger 1-2. No secret value was printed, logged, committed, or attached.
- The supported one-member ZIP uploader reported completion. The previously configured nonempty numeric LAN callback host remained present on read-back.
- Restarted only APCSurge with no pending command.
- The diagnostic candidate rejected the uploaded file with bounded reason code `BOOTSTRAP_MODE`.
- Per the safety boundary, no protection was weakened and no alternate or unsupported installation path was attempted.
- The rejected secret was overwritten through the same supported uploader with a non-secret `{}` placeholder. The uploader reported completion.

## Passive result and safety accounting

- Final acceptance topology was not reached: the slot remained Connected with zero nodes instead of one controller plus six children.
- Existing APCBridge was not modified or removed.
- APC output commands: 0.
- Loads operated: 0.
- Operator-side plaintext JSON, upload archives, and temporary bootstrap scripts were removed after the retry; the original encrypted recovery source was retained.

## Blocker

PG3x extraction does not preserve the required owner-only mode for the uploaded bootstrap (`BOOTSTRAP_MODE`). Supported UI exposes no mechanism to repair or verify the extracted file mode. Human/UDI-supported guidance is required before another secret import attempt; weakening the protected-file check or bypassing PG3x is prohibited.
