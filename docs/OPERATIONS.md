# Operations

## Add a device or interface without resetting the baseline

1. Confirm LibreNMS has fresh ARP records for the intended device and exact `ifName`.
2. Pause the timer and let any active service execution finish.
3. Back up configuration/state, then edit only the monitored interface mapping.
4. Validate config and approve each reviewed legitimate endpoint at its scope.
5. Run a dry-run, review anomalies, then resume the timer.

```bash
sudo systemctl stop arp-watcher.timer
sudo systemctl status arp-watcher.service --no-pager
sudo nano /etc/arp-watcher/config.json
```

Example configuration fragment:

```json
"monitored_interfaces": {
  "1": ["inside"],
  "2": ["internal1", "internal2"],
  "3": ["Vlan20"]
}
```

Preserve existing IDs/interfaces. Adding a scope does not automatically approve
its existing endpoints. Validate the edited settings:

```bash
sudo -u arpwatcher /opt/arp-watcher/arp_watcher.py \
  --config /etc/arp-watcher/config.json --validate-config
```

Approve each authorized IP/MAC separately (example values):

```bash
sudo -u arpwatcher /opt/arp-watcher/arp_watcher.py \
  --config /etc/arp-watcher/config.json \
  --approve 3 02:00:00:00:00:30 192.0.2.30 \
  --interface Vlan20 --label "Approved lab workstation"
```

The first argument is the firewall/router's LibreNMS device ID, not the endpoint's
ID. `--interface` is required if the device has multiple configured interfaces;
otherwise it can be inferred. Approving another IP updates that scope/MAC's one
approved address. Approving at a new scope does not revoke the previous approval.

```bash
sudo -u arpwatcher /opt/arp-watcher/arp_watcher.py \
  --config /etc/arp-watcher/config.json --list
sudo -u arpwatcher /opt/arp-watcher/arp_watcher.py \
  --config /etc/arp-watcher/config.json --dry-run
sudo systemctl start arp-watcher.timer
sudo systemctl start arp-watcher.service
sudo journalctl -u arp-watcher.service -n 30 --no-pager
```

No service-unit reload is required for config-only changes. If validation fails,
fix the config or restore the prior config before resuming.

## CLI reference

| Option | Effect |
| --- | --- |
| `--version` | Show code version |
| `--config PATH` | Select config; defaults to `/etc/arp-watcher/config.json` |
| `--validate-config` | Validate scope format and print selected pairs; no DB access |
| `--initialize` | One-time approval of current selected ARP rows; refuses existing baseline |
| `--list` | List approved mappings, including retained unmonitored scopes |
| `--dry-run` | Print anomalies without changing approvals/alert rows or sending email |
| `--approve DEVICE_ID MAC IP` | Insert/update an approved mapping |
| `--interface NAME` | Approval interface; valid only with `--approve` |
| `--label TEXT` | Optional approval description |
| No action option | Run live comparison and notification processing |

Actions are mutually exclusive. Dry-run returns 0 for no anomalies, 2 for detected
anomalies and 1 for execution errors. State connection and lock infrastructure can
still be created by a dry-run; do not mistake it for an entirely filesystem-read-only command.

## Removed interfaces and devices

Removing a configured scope stops observing it and freezes its active alerts.
It does not delete approvals/history or send a false recovery. Re-adding the scope
resumes evaluation against retained approvals. Old approvals can still inform
cross-scope MAC detection. There is no built-in revoke/delete-approval command.

## Backup

Pause the timer, wait for the service to finish, and ensure no manual watcher
commands are executing. Copy the entire state directory to preserve SQLite WAL
sidecar files alongside the database:

```bash
sudo systemctl stop arp-watcher.timer
sudo systemctl status arp-watcher.service --no-pager
```

Continue only after the service is inactive:

```bash
arp_backup_dir=$(sudo mktemp -d /var/backups/arp-watcher.XXXXXX)
sudo cp -a /etc/arp-watcher "$arp_backup_dir/config"
sudo cp -a /var/lib/arp-watcher "$arp_backup_dir/state"
sudo cp -a /opt/arp-watcher/arp_watcher.py "$arp_backup_dir/arp_watcher.py"
printf 'Backup location: %s\n' "$arp_backup_dir"
sudo systemctl start arp-watcher.timer
```

Keep the backup private and test recovery in a separate environment. Do not copy
only `state.sqlite3` while the watcher is writing. Restoring stale state can resend
alerts or lose approvals; review the target and restore point before replacement.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| MariaDB access denied | Test as `arpwatcher` with the same defaults file; check password quoting and account host |
| No ARP rows for a scope | Verify `device_id`, exact `ifName`, LibreNMS discovery, and ARP freshness |
| Unknown-MAC alerts after onboarding | Review and explicitly approve existing legitimate mappings |
| No email despite service success | Check journal, SMTP logs, recipients and notification settings |
| Timer active, service inactive | Normal after a successful oneshot execution; inspect last exit/logs |
| Another instance already running | A live process holds the lock; investigate before retrying; do not delete the lock file |
| Initialization refused | Baseline already exists; use `--approve`, not database deletion |
| Interface required for approval | Add `--interface` for a device monitoring more than one interface |

Mail failures are logged and may not fail the whole service. Do not enable a
notification blackout merely to preview changes; use `--dry-run`. All approval,
state and diagnostic output can contain sensitive network inventory.
