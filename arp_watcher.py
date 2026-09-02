#!/usr/bin/env python3
"""Persistent ARP baseline watcher for LibreNMS.

Reads current ARP entries from LibreNMS, compares them with an approved SQLite
baseline, and sends HTML email for:
  * unknown MAC addresses (critical)
  * known MAC addresses on a new device/interface (critical)
  * known MAC addresses using a different IP address (warning)

The script is designed to run as a systemd oneshot service every five minutes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import html
import json
import logging
import smtplib
import sqlite3
import ssl
import subprocess
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = "/etc/arp-watcher/config.json"
__version__ = "0.1.0"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def normalize_mac(value: str) -> str:
    mac = "".join(ch for ch in value.lower() if ch in "0123456789abcdef")
    if len(mac) != 12:
        raise ValueError(f"Invalid MAC address: {value!r}")
    return mac


def display_mac(value: str) -> str:
    mac = normalize_mac(value)
    return ":".join(mac[pos : pos + 2] for pos in range(0, 12, 2))


def sql_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


@dataclass(frozen=True)
class ArpEntry:
    device_id: int
    hostname: str
    sys_name: str
    port_id: int
    if_name: str
    ip: str
    mac: str


@dataclass(frozen=True)
class Anomaly:
    event_type: str
    severity: str
    entry: ArpEntry
    expected_ip: str = ""
    expected_location: str = ""

    @property
    def event_key(self) -> str:
        material = "|".join(
            [
                self.event_type,
                str(self.entry.device_id),
                self.entry.if_name,
                self.entry.mac,
                self.entry.ip,
            ]
        )
        return hashlib.sha256(material.encode()).hexdigest()


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)

    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")
    required = ["mysql_defaults_file", "state_db", "lock_file"]
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"Missing configuration keys: {', '.join(missing)}")

    if "monitored_interfaces" in config:
        if "device_ids" in config or "interface" in config:
            raise ValueError("Use monitored_interfaces OR device_ids/interface, not both")
        scopes = config["monitored_interfaces"]
        if not isinstance(scopes, dict) or not scopes:
            raise ValueError("monitored_interfaces must be a non-empty object")
    else:
        ids = config.get("device_ids")
        if not isinstance(ids, list) or not ids:
            raise ValueError("Provide monitored_interfaces or non-empty device_ids and interface")
        scopes = {str(value): [config.get("interface")] for value in ids}

    normalized: dict[int, list[str]] = {}
    for raw_id, interfaces in scopes.items():
        if not isinstance(raw_id, str) or not raw_id.isascii() or not raw_id.isdecimal():
            raise ValueError(f"Invalid device ID: {raw_id!r}")
        device_id = int(raw_id)
        if device_id <= 0 or device_id in normalized:
            raise ValueError(f"Invalid or duplicate device ID: {raw_id!r}")
        if not isinstance(interfaces, list) or not interfaces:
            raise ValueError(f"Device {device_id}: interfaces must be a non-empty list")
        if any(not isinstance(name, str) or not name.strip() for name in interfaces):
            raise ValueError(f"Device {device_id}: interface names must be non-empty strings")
        normalized[device_id] = list(dict.fromkeys(name.strip() for name in interfaces))
    config["monitored_interfaces"] = normalized
    return config


def connect_state(path: str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS approved (
            device_id INTEGER NOT NULL,
            if_name TEXT NOT NULL,
            mac TEXT NOT NULL,
            approved_ip TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            approved_at TEXT NOT NULL,
            PRIMARY KEY (device_id, if_name, mac)
        );

        CREATE INDEX IF NOT EXISTS idx_approved_mac ON approved(mac);

        CREATE TABLE IF NOT EXISTS alerts (
            event_key TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            device_id INTEGER NOT NULL,
            hostname TEXT NOT NULL,
            sys_name TEXT NOT NULL,
            if_name TEXT NOT NULL,
            mac TEXT NOT NULL,
            expected_ip TEXT NOT NULL DEFAULT '',
            observed_ip TEXT NOT NULL,
            expected_location TEXT NOT NULL DEFAULT '',
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            missing_runs INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            notified_at TEXT,
            resolved_at TEXT
        );
        """
    )
    return conn


def query_current_arp(config: dict[str, Any]) -> list[ArpEntry]:
    # Hex literals preserve exact interface names without depending on SQL escaping modes.
    clauses = []
    for device_id, names in sorted(config["monitored_interfaces"].items()):
        interfaces = ",".join("0x" + name.encode("utf-8").hex() for name in names)
        clauses.append(f"(d.device_id = {device_id} AND p.ifName IN ({interfaces}))")
    scope_filter = " OR ".join(clauses)
    query = f"""
        SELECT d.device_id,
               d.hostname,
               COALESCE(d.sysName, ''),
               p.port_id,
               p.ifName,
               a.ipv4_address,
               a.mac_address
        FROM ipv4_mac AS a
        JOIN devices AS d ON a.device_id = d.device_id
        JOIN ports AS p
          ON a.port_id = p.port_id
         AND a.device_id = p.device_id
        WHERE ({scope_filter})
        ORDER BY d.device_id, a.ipv4_address, a.mac_address
    """

    command = [
        "mysql",
        f"--defaults-extra-file={config['mysql_defaults_file']}",
        "--batch",
        "--raw",
        "--skip-column-names",
        "librenms",
        "-e",
        query,
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"MariaDB query failed: {result.stderr.strip()}")

    entries: list[ArpEntry] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            logging.warning("Skipping unexpected database row: %r", line)
            continue
        entries.append(
            ArpEntry(
                device_id=int(fields[0]),
                hostname=fields[1],
                sys_name=fields[2] or fields[1],
                port_id=int(fields[3]),
                if_name=fields[4],
                ip=fields[5],
                mac=normalize_mac(fields[6]),
            )
        )
    observed_scopes = {(item.device_id, item.if_name) for item in entries}
    for device_id, names in config["monitored_interfaces"].items():
        for name in names:
            if (device_id, name) not in observed_scopes:
                logging.warning(
                    "No ARP rows returned for device=%s interface=%s; "
                    "verify interface naming, discovery and data freshness", device_id, name
                )
    return entries


def initialize_baseline(conn: sqlite3.Connection, entries: list[ArpEntry]) -> None:
    existing = conn.execute("SELECT COUNT(*) FROM approved").fetchone()[0]
    if existing:
        raise RuntimeError(
            f"Baseline already contains {existing} mappings; initialization refused"
        )
    timestamp = utc_now()
    conn.executemany(
        """
        INSERT INTO approved
            (device_id, if_name, mac, approved_ip, label, approved_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                item.device_id,
                item.if_name,
                item.mac,
                item.ip,
                item.sys_name,
                timestamp,
            )
            for item in entries
        ],
    )
    conn.commit()
    print(f"Initialized approved baseline with {len(entries)} mappings.")


def approve_mapping(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    device_id: int,
    mac: str,
    ip: str,
    label: str,
    interface: str | None = None,
) -> None:
    interfaces = config["monitored_interfaces"].get(device_id)
    if not interfaces:
        raise ValueError(f"Device ID {device_id} is not configured for monitoring")
    if interface is None:
        if len(interfaces) != 1:
            raise ValueError(f"Device {device_id} has multiple interfaces; specify --interface")
        interface = interfaces[0]
    if interface not in interfaces:
        raise ValueError(f"Interface {interface!r} is not monitored on device {device_id}")
    normalized = normalize_mac(mac)
    conn.execute(
        """
        INSERT INTO approved
            (device_id, if_name, mac, approved_ip, label, approved_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_id, if_name, mac)
        DO UPDATE SET
            approved_ip = excluded.approved_ip,
            label = excluded.label,
            approved_at = excluded.approved_at
        """,
        (
            device_id,
            interface,
            normalized,
            ip,
            label,
            utc_now(),
        ),
    )
    conn.commit()
    print(
        f"Approved device={device_id} interface={interface} "
        f"MAC={display_mac(normalized)} IP={ip}"
    )


def list_baseline(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT device_id, if_name, mac, approved_ip, label, approved_at
        FROM approved
        ORDER BY device_id, if_name, approved_ip, mac
        """
    ).fetchall()
    print("DEVICE\tINTERFACE\tMAC\tAPPROVED_IP\tLABEL\tAPPROVED_AT")
    for row in rows:
        print(
            f"{row['device_id']}\t{row['if_name']}\t{display_mac(row['mac'])}\t"
            f"{row['approved_ip']}\t{row['label']}\t{row['approved_at']}"
        )


def classify(conn: sqlite3.Connection, entries: list[ArpEntry]) -> list[Anomaly]:
    anomalies: list[Anomaly] = []
    for entry in entries:
        approved_here = conn.execute(
            """
            SELECT approved_ip, label
            FROM approved
            WHERE device_id = ? AND if_name = ? AND mac = ?
            """,
            (entry.device_id, entry.if_name, entry.mac),
        ).fetchone()

        if approved_here:
            if approved_here["approved_ip"] != entry.ip:
                anomalies.append(
                    Anomaly(
                        event_type="KNOWN_MAC_NEW_IP",
                        severity="WARNING",
                        entry=entry,
                        expected_ip=approved_here["approved_ip"],
                        expected_location=f"device {entry.device_id}/{entry.if_name}",
                    )
                )
            continue

        approved_elsewhere = conn.execute(
            """
            SELECT device_id, if_name, approved_ip, label
            FROM approved
            WHERE mac = ?
            ORDER BY device_id, if_name
            LIMIT 1
            """,
            (entry.mac,),
        ).fetchone()

        if approved_elsewhere:
            expected_location = (
                f"{approved_elsewhere['label'] or 'device ' + str(approved_elsewhere['device_id'])}"
                f"/{approved_elsewhere['if_name']}"
            )
            anomalies.append(
                Anomaly(
                    event_type="MAC_MOVED",
                    severity="CRITICAL",
                    entry=entry,
                    expected_ip=approved_elsewhere["approved_ip"],
                    expected_location=expected_location,
                )
            )
        else:
            anomalies.append(
                Anomaly(
                    event_type="UNKNOWN_MAC",
                    severity="CRITICAL",
                    entry=entry,
                )
            )
    return anomalies


def event_title(event_type: str) -> str:
    return {
        "UNKNOWN_MAC": "Unknown MAC detected",
        "KNOWN_MAC_NEW_IP": "Known MAC received a new IP",
        "MAC_MOVED": "Known MAC moved to another site/interface",
    }.get(event_type, event_type)


def build_email(row: sqlite3.Row, recovered: bool) -> tuple[str, str]:
    status = "RECOVERED" if recovered else "ACTIVE"
    severity = row["severity"]
    color = "#15803d" if recovered else ("#b91c1c" if severity == "CRITICAL" else "#d97706")
    title = event_title(row["event_type"])
    subject = f"[{severity}][{status}] {title} | {row['sys_name']}/{row['if_name']}"

    details: list[tuple[str, str]] = [
        ("Site/device", row["sys_name"]),
        ("Management address", row["hostname"]),
        ("Interface", row["if_name"]),
        ("MAC address", display_mac(row["mac"])),
        ("Observed IP", row["observed_ip"]),
    ]
    if row["expected_ip"]:
        details.append(("Approved IP", row["expected_ip"]))
    if row["expected_location"]:
        details.append(("Approved location", row["expected_location"]))
    details.extend(
        [
            ("First observed", row["first_seen"]),
            ("Last observed", row["last_seen"]),
        ]
    )

    table_rows = "".join(
        "<tr>"
        f"<td style='padding:7px 10px;font-weight:bold;color:#4b5563;border-bottom:1px solid #e5e7eb'>{html.escape(label)}</td>"
        f"<td style='padding:7px 10px;border-bottom:1px solid #e5e7eb'>{html.escape(str(value))}</td>"
        "</tr>"
        for label, value in details
    )
    recommendation = (
        "The ARP anomaly is no longer present. Confirm whether the mapping was approved or aged out."
        if recovered
        else "Verify the device and DHCP lease. Approve the mapping only after confirming ownership and location."
    )
    body = f"""<!doctype html>
<html><body style="margin:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;color:#1f2937">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:20px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:760px;margin:0 auto;background:#ffffff;border:1px solid #d1d5db;border-collapse:collapse">
<tr><td style="padding:18px 22px;background:{color};color:#fff;font-size:21px;font-weight:bold">{status} — {severity}</td></tr>
<tr><td style="padding:20px 22px"><div style="font-size:19px;font-weight:bold;margin-bottom:14px">{html.escape(title)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:14px">{table_rows}</table>
<div style="margin-top:18px;padding:12px;background:#fef3c7;border-left:4px solid #d97706;font-size:13px">{html.escape(recommendation)}</div>
</td></tr>
<tr><td style="padding:14px 22px;background:#f3f4f6;color:#6b7280;font-size:12px">Generated automatically by LibreNMS ARP Watcher.</td></tr>
</table></td></tr></table></body></html>"""
    return subject, body


def send_email(config: dict[str, Any], subject: str, body: str) -> None:
    notifications = config.get("notifications", {})
    if not notifications.get("enabled", True):
        logging.info("Email disabled; would send: %s", subject)
        return

    smtp = config["smtp"]
    recipients = smtp.get("recipients", [])
    if not recipients:
        raise ValueError("smtp.recipients cannot be empty when notifications are enabled")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp["sender"]
    message["To"] = ", ".join(recipients)
    message.set_content("This alert requires an HTML-capable mail client.")
    message.add_alternative(body, subtype="html")

    context = ssl.create_default_context()
    if smtp.get("ssl", False):
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            smtp["host"], int(smtp.get("port", 465)), timeout=15, context=context
        )
    else:
        client = smtplib.SMTP(smtp["host"], int(smtp.get("port", 25)), timeout=15)
    try:
        if smtp.get("starttls", False):
            client.starttls(context=context)
        username = smtp.get("username", "")
        if username:
            client.login(username, smtp.get("password", ""))
        client.send_message(message)
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def row_for_anomaly(conn: sqlite3.Connection, event_key: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM alerts WHERE event_key = ?", (event_key,)).fetchone()
    if row is None:
        raise RuntimeError(f"Missing alert row {event_key}")
    return row


def process_run(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    entries: list[ArpEntry],
    dry_run: bool,
) -> int:
    anomalies = classify(conn, entries)
    now = utc_now()
    seen = {item.event_key for item in anomalies}

    if dry_run:
        if not anomalies:
            print("No ARP anomalies detected.")
        for item in anomalies:
            print(
                f"{item.severity} {item.event_type}: {item.entry.sys_name}/"
                f"{item.entry.if_name} {display_mac(item.entry.mac)} {item.entry.ip}"
            )
        return 2 if anomalies else 0

    for anomaly in anomalies:
        existing = conn.execute(
            "SELECT active FROM alerts WHERE event_key = ?", (anomaly.event_key,)
        ).fetchone()
        if existing and existing["active"]:
            conn.execute(
                "UPDATE alerts SET last_seen = ?, missing_runs = 0 WHERE event_key = ?",
                (now, anomaly.event_key),
            )
        else:
            conn.execute(
                """
                INSERT INTO alerts (
                    event_key, event_type, severity, device_id, hostname,
                    sys_name, if_name, mac, expected_ip, observed_ip,
                    expected_location, first_seen, last_seen, missing_runs,
                    active, notified_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, NULL, NULL)
                ON CONFLICT(event_key) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    missing_runs = 0,
                    active = 1,
                    notified_at = NULL,
                    resolved_at = NULL
                """,
                (
                    anomaly.event_key,
                    anomaly.event_type,
                    anomaly.severity,
                    anomaly.entry.device_id,
                    anomaly.entry.hostname,
                    anomaly.entry.sys_name,
                    anomaly.entry.if_name,
                    anomaly.entry.mac,
                    anomaly.expected_ip,
                    anomaly.entry.ip,
                    anomaly.expected_location,
                    now,
                    now,
                ),
            )
        conn.commit()

        row = row_for_anomaly(conn, anomaly.event_key)
        if row["notified_at"] is None:
            try:
                subject, body = build_email(row, recovered=False)
                send_email(config, subject, body)
                conn.execute(
                    "UPDATE alerts SET notified_at = ? WHERE event_key = ?",
                    (utc_now(), anomaly.event_key),
                )
                conn.commit()
                logging.warning("New ARP anomaly: %s", subject)
            except Exception:
                logging.exception("Failed sending active alert for %s", anomaly.event_key)

    resolve_after = max(1, int(config.get("resolve_after_missing_runs", 2)))
    active_rows = conn.execute("SELECT * FROM alerts WHERE active = 1").fetchall()
    for row in active_rows:
        if row["if_name"] not in config["monitored_interfaces"].get(row["device_id"], []):
            # Removing a monitored scope is not evidence of recovery. Keep its history.
            continue
        if row["event_key"] in seen:
            continue
        missing = row["missing_runs"] + 1
        conn.execute(
            "UPDATE alerts SET missing_runs = ? WHERE event_key = ?",
            (missing, row["event_key"]),
        )
        conn.commit()
        if missing < resolve_after:
            continue

        refreshed = row_for_anomaly(conn, row["event_key"])
        if refreshed["notified_at"] is not None:
            try:
                subject, body = build_email(refreshed, recovered=True)
                send_email(config, subject, body)
                logging.info("ARP anomaly recovered: %s", subject)
            except Exception:
                logging.exception("Failed sending recovery for %s", row["event_key"])
                continue
        conn.execute(
            """
            UPDATE alerts
            SET active = 0, resolved_at = ?, missing_runs = ?
            WHERE event_key = ?
            """,
            (utc_now(), missing, row["event_key"]),
        )
        conn.commit()

    logging.info(
        "ARP watcher completed: %d current entries, %d active anomalies",
        len(entries),
        len(anomalies),
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--initialize", action="store_true", help="Trust current ARP entries as the initial baseline")
    action.add_argument("--list", action="store_true", help="List approved mappings")
    action.add_argument("--dry-run", action="store_true", help="Show anomalies without changing state or sending email")
    action.add_argument("--approve", nargs=3, metavar=("DEVICE_ID", "MAC", "IP"), help="Approve or update a mapping")
    action.add_argument("--validate-config", action="store_true", help="Validate and list monitored interfaces without DB access or email")
    parser.add_argument("--interface", help="Interface for --approve; required if a device has multiple interfaces")
    parser.add_argument("--label", default="", help="Optional label used with --approve")
    args = parser.parse_args()
    if args.interface is not None and not args.approve:
        parser.error("--interface is only valid with --approve")
    return args


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    try:
        config = load_config(args.config)
        if args.validate_config:
            for device_id, names in sorted(config["monitored_interfaces"].items()):
                print(f"Device {device_id}: {', '.join(names)}")
            print("Configuration valid. No database access or email performed.")
            return 0
        lock_path = Path(config["lock_file"])
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                logging.warning("Another ARP watcher instance is already running")
                return 0

            conn = connect_state(config["state_db"])
            try:
                if args.list:
                    list_baseline(conn)
                    return 0
                if args.approve:
                    approve_mapping(
                        conn,
                        config,
                        int(args.approve[0]),
                        args.approve[1],
                        args.approve[2],
                        args.label,
                        args.interface,
                    )
                    return 0

                entries = query_current_arp(config)
                if args.initialize:
                    if not entries:
                        raise RuntimeError("No current ARP entries found; baseline not initialized")
                    initialize_baseline(conn, entries)
                    return 0
                return process_run(conn, config, entries, args.dry_run)
            finally:
                conn.close()
    except Exception as exc:
        logging.error("ARP watcher failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
