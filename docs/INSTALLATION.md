# Installation (Ubuntu 24.04 reference)

Run these steps only on a server you administer. Commands assume the repository root is your
working directory and LibreNMS uses a local MariaDB database named `librenms`.
Other deployments require adapting database connectivity and paths.

## 1. Prerequisites

```bash
sudo apt update
sudo apt install python3 mariadb-client util-linux
python3 --version
mysql --version
runuser --version
flock --version
```

Before installing, confirm LibreNMS already stores ARP rows for the intended
devices/interfaces. The supplied combined cycle refreshes those rows by invoking
LibreNMS discovery; it cannot make a firewall expose an ARP table that its SNMP
agent does not provide.

```bash
sudo mariadb -D librenms -e "
SELECT d.device_id, d.hostname, p.ifName,
       a.ipv4_address, a.mac_address
FROM ipv4_mac a
JOIN devices d ON d.device_id=a.device_id
JOIN ports p ON p.port_id=a.port_id AND p.device_id=a.device_id
WHERE d.device_id IN (1,2)
ORDER BY d.device_id,p.ifName,a.ipv4_address;"
```

Replace device IDs 1 and 2 with your selected IDs. Check the exact `ifName` values
and verify data is being refreshed. Keep this output private.

## 2. Service account and protected directories

Create the account only if it does not already exist:

```bash
sudo useradd --system --home /var/lib/arp-watcher --shell /usr/sbin/nologin arpwatcher
sudo install -d -o root -g arpwatcher -m 0750 /opt/arp-watcher
sudo install -d -o root -g arpwatcher -m 0750 /etc/arp-watcher
sudo install -d -o arpwatcher -g arpwatcher -m 0750 /var/lib/arp-watcher
sudo install -o root -g arpwatcher -m 0750 arp_watcher.py /opt/arp-watcher/arp_watcher.py
sudo install -o root -g arpwatcher -m 0640 examples/config.json.example /etc/arp-watcher/config.json
sudo install -o root -g arpwatcher -m 0640 examples/mysql.cnf.example /etc/arp-watcher/mysql.cnf
```

These copy commands are for a fresh installation. Do not overwrite an existing
live configuration with examples.

## 3. Read-only MariaDB account

Use the configured MariaDB administrator account. For local socket-admin setups:

```bash
sudo mariadb
```

In the database prompt, substitute a unique strong password:

```sql
CREATE USER 'arpwatcher'@'localhost'
  IDENTIFIED BY 'REPLACE_WITH_A_STRONG_DATABASE_PASSWORD';
GRANT SELECT ON librenms.ipv4_mac TO 'arpwatcher'@'localhost';
GRANT SELECT ON librenms.devices TO 'arpwatcher'@'localhost';
GRANT SELECT ON librenms.ports TO 'arpwatcher'@'localhost';
EXIT;
```

Do not paste real passwords into issue reports or screenshots. Account statements
can be recorded in database/client history; follow your organization's secret-handling policy.

Edit the credential file and replace the placeholder with that same password:

```bash
sudo nano /etc/arp-watcher/mysql.cnf
```

Use valid MariaDB option-file quoting for special characters. This database
password is unrelated to the Linux service account. Do not put it in the systemd
unit or in the repository.

```bash
sudo -u arpwatcher mysql \
  --defaults-extra-file=/etc/arp-watcher/mysql.cnf \
  --batch --skip-column-names \
  -e "SELECT COUNT(*) FROM librenms.ipv4_mac;"
```

The total count verifies access, not selected-interface coverage.

## 4. Configuration and SMTP

```bash
sudo nano /etc/arp-watcher/config.json
```

Replace the example scopes with your LibreNMS device IDs and exact interface
names. Configure sender, recipients and SMTP parameters. The provided sample
uses authenticated STARTTLS on port 587; it is not a working mail account.

| Mail setup | `port` | `starttls` | `ssl` |
| --- | --- | --- | --- |
| STARTTLS submission | 587 | true | false |
| Implicit TLS | 465 | false | true |
| Approved internal relay without TLS | 25 | false | false |

Never enable both TLS modes. For an unauthenticated relay use empty strings for
`username` and `password`; limit relay access to the watcher host. Do not disable
certificate validation to work around certificate errors.

```bash
sudo chown root:arpwatcher /etc/arp-watcher/config.json /etc/arp-watcher/mysql.cnf
sudo chmod 640 /etc/arp-watcher/config.json /etc/arp-watcher/mysql.cnf
sudo -u arpwatcher /opt/arp-watcher/arp_watcher.py \
  --config /etc/arp-watcher/config.json --validate-config
```

Config validation checks required paths and device/interface selection. It does
not validate connectivity, SMTP delivery or every SMTP parameter.

## 5. Initialize once after review

Initialization approves all currently returned selected mappings. Review them
against an authorized inventory/DHCP source first. No email is sent by initialization.

```bash
sudo -u arpwatcher /opt/arp-watcher/arp_watcher.py \
  --config /etc/arp-watcher/config.json --initialize
sudo -u arpwatcher /opt/arp-watcher/arp_watcher.py \
  --config /etc/arp-watcher/config.json --list
sudo -u arpwatcher /opt/arp-watcher/arp_watcher.py \
  --config /etc/arp-watcher/config.json --dry-run
```

Initialization refuses a non-empty baseline or zero returned entries. A partial
ARP table can still produce a partial initial baseline; review scope coverage.
Future legitimate entries require explicit approval. A normal run does not
initialize a baseline automatically.

## 6. Install the five-minute refresh cycle

The recommended schedule refreshes LibreNMS ARP data first and runs the watcher
only after every selected device succeeds. Install the watcher-only service for
manual testing, plus the combined script, service and timer:

```bash
sudo install -o root -g root -m 0644 deploy/systemd/arp-watcher.service /etc/systemd/system/arp-watcher.service
sudo install -o root -g root -m 0750 scripts/arp-monitor-cycle /usr/local/sbin/arp-monitor-cycle
sudo install -o root -g root -m 0644 deploy/systemd/arp-monitor-cycle.service /etc/systemd/system/arp-monitor-cycle.service
sudo install -o root -g root -m 0644 deploy/systemd/arp-monitor-cycle.timer /etc/systemd/system/arp-monitor-cycle.timer
sudo systemctl daemon-reload
sudo bash -n /usr/local/sbin/arp-monitor-cycle
sudo systemctl start arp-monitor-cycle.service
sudo journalctl -u arp-monitor-cycle.service -n 100 --no-pager
```

Confirm the log shows each configured device, `ARP table refresh completed
successfully`, the watcher result, and `ARP monitoring cycle completed
successfully`. Then enable scheduling:

```bash
sudo systemctl disable --now arp-watcher.timer 2>/dev/null || true
sudo systemctl enable --now arp-monitor-cycle.timer
sudo systemctl status arp-monitor-cycle.timer --no-pager
sudo systemctl list-timers arp-monitor-cycle.timer --all
```

The oneshot service normally returns to `inactive (dead)` after success. The
timer remains `active (waiting)`. It targets exact five-minute clock boundaries;
systemd and `flock` prevent overlap. The standalone `arp-watcher.timer` is a
legacy option and must remain disabled while the combined timer is enabled.

See [ARP_REFRESH_CYCLE.md](ARP_REFRESH_CYCLE.md) for architecture, upgrade steps,
database verification and troubleshooting.

## 7. Validate operationally

In an authorized lab or test scope, observe an unapproved test endpoint, verify
an active notification, approve its mapping, and confirm recovery after the
configured number of runs. Do not alter production device availability to test.
Offline tests and successful systemd exits do not prove end-to-end email delivery.

Back up configuration and the complete state directory using the consistent
backup procedure in [OPERATIONS.md](OPERATIONS.md).
