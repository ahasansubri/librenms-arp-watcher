# LibreNMS ARP Watcher

**Detect unfamiliar MAC addresses, changed IP mappings, and cross-site MAC observations using the ARP data already collected by LibreNMS.**

A small Python service for SOC and network operations teams. It compares selected
device/interface pairs against an explicitly approved SQLite baseline and sends
HTML email notifications. It does not query or modify firewalls directly.

Independent community project; not an official LibreNMS product or vendor integration.

## What it detects

| Observation | Severity | Interpretation |
| --- | --- | --- |
| MAC absent from the approved baseline | Critical | Unrecognized MAC on a monitored interface |
| Approved MAC observed on an unapproved device/interface | Critical | Possible movement or duplicate observation; investigate |
| Approved MAC observed with a different IPv4 address | Warning | Mapping change, potentially a legitimate DHCP renewal |
| Anomaly absent for consecutive successful runs | Recovery | Observation cleared from the dataset, not proof of remediation |

An alert is normally emailed once when activated. Failed active-email attempts
are retried on subsequent runs. Recovery is normally emailed after two missing
runs; an anomaly that reappears after recovery can alert again.

## Features

- Per-device interface selection, including multiple interfaces per device.
- Explicit approval of MAC/IP mappings; newly observed entries are never trusted automatically.
- Persistent SQLite baseline and alert history.
- Read-only access to three LibreNMS database tables.
- SNMP-independent operation: works with devices whose ARP data LibreNMS actually stores.
- Dry-run mode, configuration validation, baseline listing and interface-specific approval.
- HTML emails with device, interface, observed mapping and expected mapping.
- A five-minute systemd timer and restricted Linux service account.
- No third-party Python runtime dependencies.
- Backward compatibility with the original single-interface configuration and SQLite schema.

## Data flow

1. LibreNMS collects device ARP data through its own discovery/polling workflow.
2. The watcher reads matching records from `ipv4_mac`, `devices` and `ports`.
3. It compares records with the local approved baseline and updates alert history.
4. An SMTP server delivers active/recovery notifications.

This is a database-based watcher, not a packet-capture daemon, NAC system or active network scanner.

## Requirements

- Linux, Python 3.10 or later (`fcntl` is required; native Windows execution is unsupported).
- A working LibreNMS installation with IPv4 ARP entries in MariaDB database `librenms`.
- A `mysql`-compatible CLI and access to the selected tables.
- SQLite support in Python; systemd for the supplied deployment units.
- A reachable SMTP server for email notifications.

Ubuntu 24.04 is the deployment reference. CI is configured for Python 3.10–3.13;
that matrix runs after the repository is pushed to GitHub.

## Configuration at a glance

```json
"monitored_interfaces": {
  "1": ["inside"],
  "2": ["internal1", "internal2"]
}
```

Keys are **LibreNMS device IDs** and values are exact `ports.ifName` strings,
not aliases or port IDs. Replace these example IDs/names with your inventory.

The complete, placeholder-only configuration is in
[examples/config.json.example](examples/config.json.example). Keep live config
outside the repository, normally under `/etc/arp-watcher/`.

## Start here

1. [Install on Linux](docs/INSTALLATION.md): accounts, least-privilege DB access, SMTP and initial baseline.
2. [Operate the watcher](docs/OPERATIONS.md): add devices, approve mappings, test and troubleshoot.

Do not reinitialize an existing baseline. Review legitimate mappings before
initialization or approval; ARP presence is not proof that an endpoint is trusted.

## Command examples

Validate configuration without DB access or sending email:

```bash
python3 arp_watcher.py --config examples/config.json.example --validate-config
```

Preview anomalies on an installed instance:

```bash
sudo -u arpwatcher /opt/arp-watcher/arp_watcher.py \
  --config /etc/arp-watcher/config.json --dry-run
```

Approve an endpoint after verifying ownership and location:

```bash
sudo -u arpwatcher /opt/arp-watcher/arp_watcher.py \
  --config /etc/arp-watcher/config.json \
  --approve 2 02:00:00:00:00:20 192.0.2.20 \
  --interface internal2 --label "Approved lab workstation"
```

The IP/MAC above are illustrative. Approval inserts or updates one mapping; it
does not modify the firewall, DHCP service, LibreNMS or other approvals.

## Test locally

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check_repository.py
```

Tests use temporary SQLite databases and mock MariaDB/SMTP access. They send no
email and do not require network devices or a production database.

## Important limitations

- Detection speed is bounded by LibreNMS ARP freshness. Running the watcher every
  five minutes does not force LibreNMS to refresh its ARP table every five minutes.
- ARP is opportunistic. Quiet or disconnected endpoints may not appear. Missing
  ARP entries are not device-down alerts.
- Recovery means an anomaly disappeared from stored observations. ARP aging,
  discovery gaps or changed scope can affect visibility. Query failures abort a
  run, but a successful empty query cannot prove network health.
- A MAC address is not a trustworthy identity: spoofing, shared virtual MACs,
  HA and randomized client MACs can create false positives or blind spots.
- Device/interface/IP/MAC are tracked; SNMP context/VRF is not part of the key.
  Do not use this version where contexts cause ambiguous mappings on a single scope.
- One approved IP is stored per device/interface/MAC. Multi-IP endpoints require
  policy decisions or a future extension. IPv6 NDP is not supported.
- There is no bulk onboarding approval, endpoint blocking, web UI or LibreNMS
  alert-rule integration. Notifications use this project's SMTP configuration.
- Turning notifications off suppresses sends but still advances notification
  bookkeeping. Use `--dry-run` for previews; do not expect old suppressed events
  to be emailed automatically when notifications are re-enabled.
- SMTP success means server acceptance, not inbox delivery. Partial recipient
  refusal is not individually tracked by this version. Mail errors are logged;
  a completed run can still exit 0, so review delivery logs as well as unit status.

## Security and publication

Read [SECURITY.md](SECURITY.md) before deployment or sharing evidence.
Real configuration, baseline databases, credentials, packet captures, production
screenshots and network inventory do not belong in this repository.

## License

No license has been selected for this prepared repository. The owner must confirm
publication rights and add an appropriate license before presenting this as an
open-source release. Public visibility alone does not grant general reuse rights.
