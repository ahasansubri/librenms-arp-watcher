# Five-minute ARP refresh cycle

## Why this cycle exists

LibreNMS normally learns firewall ARP entries through the `arp-table` discovery
module and stores them in `ipv4_mac`. A five-minute watcher by itself only reads
the rows already in that table; it does not make them fresh.

The supplied cycle solves that gap in this order:

1. Read the monitored LibreNMS device IDs from the watcher configuration.
2. Run only LibreNMS `arp-table` discovery for every selected device.
3. Stop immediately if any discovery fails.
4. Run the watcher only after all selected devices were refreshed successfully.

LibreNMS performs the SNMP query using each device's existing LibreNMS SNMP
configuration. The watcher still has only read-only database access.

## Installed components

| Component | Purpose |
| --- | --- |
| `/usr/local/sbin/arp-monitor-cycle` | Orchestrates discovery and watcher execution |
| `arp-monitor-cycle.service` | Runs one complete cycle and prevents overlap with `flock` |
| `arp-monitor-cycle.timer` | Starts the cycle at clock minutes 00, 05, 10, and so on |
| `arp-watcher.service` | Retained for manual watcher-only testing |
| `arp-watcher.timer` | Legacy standalone schedule; keep disabled when using the combined cycle |

The orchestration service starts as root only so it can switch users safely:

- LibreNMS discovery runs as `librenms`.
- The baseline watcher runs as `arpwatcher`.

No database or SMTP credential is placed in a systemd unit.

## Install or upgrade the cycle

Run from the repository root:

```bash
sudo install -o root -g root -m 0750 \
  scripts/arp-monitor-cycle /usr/local/sbin/arp-monitor-cycle

sudo install -o root -g root -m 0644 \
  deploy/systemd/arp-monitor-cycle.service \
  /etc/systemd/system/arp-monitor-cycle.service

sudo install -o root -g root -m 0644 \
  deploy/systemd/arp-monitor-cycle.timer \
  /etc/systemd/system/arp-monitor-cycle.timer
```

Disable the old independent timer so it cannot evaluate stale data while a
refresh cycle is running:

```bash
sudo systemctl disable --now arp-watcher.timer
sudo systemctl daemon-reload
```

Test one complete cycle before enabling its timer:

```bash
sudo bash -n /usr/local/sbin/arp-monitor-cycle
sudo systemctl start arp-monitor-cycle.service
sudo systemctl status arp-monitor-cycle.service --no-pager
sudo journalctl -u arp-monitor-cycle.service -n 100 --no-pager
```

A successful oneshot service normally shows `inactive (dead)` after it exits,
along with `status=0/SUCCESS`. This is expected.

Enable the timer only after the manual run succeeds:

```bash
sudo systemctl enable --now arp-monitor-cycle.timer
systemctl list-timers arp-monitor-cycle.timer --all
```

## Validate the selected devices

The cycle reads device IDs from `monitored_interfaces`. It falls back to the
legacy `device_ids` array when necessary. Before enabling it, verify every ID
exists in LibreNMS:

```bash
sudo python3 - /etc/arp-watcher/config.json <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)

ids = (config["monitored_interfaces"].keys()
       if "monitored_interfaces" in config
       else config.get("device_ids", []))
print("Monitored device IDs:", ", ".join(map(str, ids)))
PY

sudo mariadb -D librenms -e "
SELECT device_id, hostname, sysName
FROM devices
ORDER BY device_id;
"
```

An absent or invalid device ID makes discovery fail, and the watcher will not
run against partially refreshed data.

## Confirm database results

Use the actual IDs from your configuration:

```bash
sudo mariadb -D librenms -e "
SELECT d.device_id, d.hostname, p.ifName,
       a.ipv4_address, a.mac_address
FROM ipv4_mac AS a
JOIN devices AS d ON d.device_id = a.device_id
JOIN ports AS p
  ON p.port_id = a.port_id
 AND p.device_id = a.device_id
WHERE d.device_id IN (1,2)
ORDER BY d.device_id, p.ifName, a.ipv4_address;
"
```

## Scheduling behavior

`OnCalendar=*-*-* *:0/5:00` targets exact five-minute clock boundaries. Normal
full LibreNMS discovery can remain on its existing schedule. Module-only
discovery updates `devices.last_discovered`, so that field will no longer tell
you when the last *full* discovery ran for these devices.

Systemd does not start a second copy while the same service remains active, and
`flock` provides an additional overlap guard. If a cycle approaches the
four-minute timeout, investigate SNMP or device latency instead of increasing
the frequency.

## Troubleshooting

| Symptom | Meaning or action |
| --- | --- |
| Timer is active; service is inactive | Normal between oneshot executions |
| Service exits non-zero during one device | Fix that LibreNMS device/SNMP problem; watcher was intentionally skipped |
| `Cannot read config.json` | Check file path and root-readable permissions |
| `lnms` not executable | Verify LibreNMS installation at `/opt/librenms` |
| `runuser` missing | Install the Ubuntu `util-linux` package |
| `flock` missing | Install the Ubuntu `util-linux` package |
| Active anomaly remains after success | Discovery worked; review the anomaly and baseline separately |

Follow live executions with:

```bash
sudo journalctl -u arp-monitor-cycle.service -f
```
