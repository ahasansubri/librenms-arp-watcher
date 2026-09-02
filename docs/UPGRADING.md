# Upgrade from an existing single- or per-device-interface watcher

The public-source layout changes file locations and neutralizes email branding.
The deployed script location, SQLite schema, event keys and CLI approval format
remain compatible. This source release adds a `--version` option and the supplied
service unit uses `UMask=0077` for newly created files.

## Preserve live settings and state

Stop the timer, wait for the oneshot to finish, and make a consistent backup using
[OPERATIONS.md](OPERATIONS.md). Keep the timer paused during the upgrade. Record
whether it was enabled/active so you can restore its previous scheduling state.

Do not overwrite `/etc/arp-watcher/config.json` or `mysql.cnf` with examples. Do
not delete `/var/lib/arp-watcher/state.sqlite3` or run `--initialize` again.

From the new repository root:

```bash
python3 -m unittest discover -s tests -v
sudo install -o root -g arpwatcher -m 0750 arp_watcher.py /opt/arp-watcher/arp_watcher.py
```

The code works with either legacy config:

```json
"device_ids": [1, 2],
"interface": "internal1"
```

or a new device/interface mapping:

```json
"monitored_interfaces": {
  "1": ["internal1"],
  "2": ["internal2"]
}
```

Replace example IDs with your inventory. **Never mix both formats.** Retain all
existing scope pairs you still intend to monitor. Configure and approve any new
scope as described in OPERATIONS.md. Existing approved pairs remain unchanged.

Validate and preview:

```bash
sudo -u arpwatcher /opt/arp-watcher/arp_watcher.py --version
sudo -u arpwatcher /opt/arp-watcher/arp_watcher.py \
  --config /etc/arp-watcher/config.json --validate-config
sudo -u arpwatcher /opt/arp-watcher/arp_watcher.py \
  --config /etc/arp-watcher/config.json --list
sudo -u arpwatcher /opt/arp-watcher/arp_watcher.py \
  --config /etc/arp-watcher/config.json --dry-run
```

The original systemd units can continue to run the script. If you choose to adopt
the included generic service/timer units, back up and review any local unit
customizations first, install from `deploy/systemd/`, then run
`sudo systemctl daemon-reload`. Do not silently overwrite locally customized units.

After review:

```bash
sudo systemctl start arp-watcher.timer
sudo systemctl start arp-watcher.service
sudo journalctl -u arp-watcher.service -n 30 --no-pager
```

## Rollback

Pause scheduling and let active executions finish. Restore the backed-up script
and corresponding config together, preserving permissions. The SQLite schema has
not changed, so do not blindly replace the current state with an old backup.
Review any newly created scopes/alerts before using older code: legacy code may
interpret unmonitored alerts as missing and generate misleading recoveries.
