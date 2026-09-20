# APCSurge 0.1.1 hardened deployment evidence

Captured: 2026-09-20T15:05:04-07:00

## Approved immutable identity and publication

- Parent review approved commit `1fc12f8108e3823151e6bcfd980303cb5efbef02`, tree `76c6168f6e63e488d41c57555036db56d4695f52`, Git archive SHA-256 `3fd6d375f3fa380b3b0978fc9ef28e37daafbe676dbb71a653b79231e07eb338`, source-distribution SHA-256 `27c92c05e1b16b82b19b48e90ebe26291642f4176425f1fe39507cbdee4fcfc4`, and wheel SHA-256 `9e2f20ed9c4ba68399da0f131b5c5b7e1e0a835d04a31b0e0e75dec56943d2dd`.
- Only that approved commit was pushed to the already-authorized public `main` branch.
- Anonymous `git ls-remote` read-back resolved public `main` exactly to the approved commit before and after deployment.
- Production and Non-production/Beta stores were not used or published.

## Supported Local Store deployment

- The private Local Store record was updated and reloaded through `https://eisy.local`.
- Persisted record read-back showed profile/purchase-option version `0.1.1`, store `Local`, and Persistent Folder `data`.
- APCSurge was reinstalled into its existing slot 3 through the supported Local Store workflow; no additional slot was created.
- Post-install slot read-back showed `Current Version: 0.1.1 / Free [Local]` and `Connected`.

## Protected import and passive result

- The retained encrypted recovery sources were opened only in the protected operator workspace. No account credential, token, device identity, LAN address, key identifier, LAN key, or secret value was printed, logged, committed, or attached.
- Recovery validation found exactly one LAN-enabled APC device and exactly six writable Boolean endpoint mappings: LED, Outlet 1-3, and USB Charger 1-2.
- The reconstructed bootstrap passed the exact approved candidate's schema parser with six endpoints and the expected `USB Charger 1` identity.
- The protected upload ZIP contained exactly one regular member, `apc-bootstrap.json`, with archive mode `0600`; the operator-side plaintext intermediary was removed before upload.
- PG3x's supported per-install ZIP uploader reported `Upload completed.` The previously validated nonempty `callback_host` was retained unchanged.
- Only APCSurge slot 3 was restarted. No command was pending or created by this work.
- The restarted plugin emitted the bounded notice `Configuration rejected [BOOTSTRAP_SCHEMA].` Passive dashboard read-back remained `Connected` with `Nodes 0`, not the required one controller plus six child nodes.
- The supported UI cannot read back the extracted bootstrap bytes, ownership, mode, or final path, so the runtime rejection cannot safely be distinguished from an extraction/overwrite/staging discrepancy. No alternate path, direct appliance access, protection weakening, or guess was attempted.

## Mandatory scrub, cleanup, and safety accounting

- On rejection, the target was overwritten through the same supported uploader with a one-member non-secret `{}` placeholder ZIP; PG3x again reported `Upload completed.`
- The operator-side plaintext, protected upload ZIP, scrub ZIP, and temporary reconstruction/validation scripts were removed. The original encrypted recovery sources were retained.
- Existing APCBridge was not altered, stopped, or removed.
- APC output commands: **0**.
- Loads operated: **0**.
- The downstream topology/hardware-validation lane remains gated because topology is still 0 rather than 1+6.

## Accepted residual platform uncertainty

PG3x does not document the complete staging-copy, extraction-overwrite, backup, restore, or reinstall lifecycle for uploaded persistent files. The supported scrub upload was completed, but this record makes no secure-erasure claim and does not assert deletion of platform-managed historical or backup copies.
