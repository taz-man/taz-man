# Configuration

The proof of concept expects a protected bootstrap file at `data/apc-bootstrap.json`. It will contain the already-provisioned strip's stable identity, current LAN address, discovered Boolean property metadata, and Ayla LAN key material.

Do not commit that file. Account credentials are not intended to remain configured after bootstrap.

Parameters:

- `bootstrap_config_path`: path under the plugin directory to protected bootstrap data.
- `callback_port`: LAN-reachable HTTP callback port advertised to the strip; default `10275`.
- `command_timeout_seconds`: maximum wait for matching device telemetry before a command fails.
- `max_command_attempts`: bounded resend count before failure.

The eisy and strip must be on mutually reachable LAN paths. No public exposure or router port forwarding is required.
