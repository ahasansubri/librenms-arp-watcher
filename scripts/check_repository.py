#!/usr/bin/env python3
"""Conservative public-source hygiene checks; not a comprehensive secret scanner."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
LIVE_NAMES = {"config.json", "mysql.cnf", ".env"}
SENSITIVE_SUFFIXES = (".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".sqlite",
                      ".db", ".key", ".pem", ".p12", ".pfx", ".pcap", ".pcapng",
                      ".backup", ".bak", ".log")
TOKEN_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]


def check(root=ROOT):
    errors = []
    required_files = (
        "scripts/arp-monitor-cycle",
        "deploy/systemd/arp-monitor-cycle.service",
        "deploy/systemd/arp-monitor-cycle.timer",
        "docs/ARP_REFRESH_CYCLE.md",
    )
    for relative in required_files:
        if not (root / relative).is_file():
            errors.append(f"Required refresh-cycle file missing: {relative}")
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in SKIP for part in relative.parts) or not path.is_file():
            continue
        if path.name in LIVE_NAMES or path.name.endswith(SENSITIVE_SUFFIXES):
            errors.append(f"Sensitive runtime file present: {relative}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            errors.append(f"Unexpected binary file: {relative}")
            continue
        for pattern in TOKEN_PATTERNS:
            if pattern.search(text):
                errors.append(f"Potential credential material: {relative}")
        if path.suffix == ".md":
            for target in re.findall(r"\]\(([^)]+)\)", text):
                if "://" in target or target.startswith(("#", "mailto:")):
                    continue
                if not (path.parent / target.split("#")[0]).exists():
                    errors.append(f"Broken relative link: {relative} -> {target}")
    try:
        cfg = json.loads((root / "examples/config.json.example").read_text())
        smtp = cfg["smtp"]
        if smtp["host"] != "smtp.example.com":
            errors.append("SMTP example host is not the approved placeholder")
        if smtp["password"] != "REPLACE_WITH_SMTP_PASSWORD":
            errors.append("SMTP example contains a non-placeholder password")
        if any(not address.endswith("@example.com") for address in [smtp["sender"], *smtp["recipients"]]):
            errors.append("SMTP example contains a non-example email address")
        cnf = (root / "examples/mysql.cnf.example").read_text()
        if "password=REPLACE_WITH_A_STRONG_DATABASE_PASSWORD" not in cnf:
            errors.append("MariaDB example placeholder password changed; review required")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"Invalid or missing example configuration: {type(exc).__name__}")
    return errors


if __name__ == "__main__":
    findings = check()
    for finding in findings:
        print(f"FAIL: {finding}")
    if not findings:
        print("Repository hygiene checks passed (not a comprehensive secret scan).")
    sys.exit(1 if findings else 0)
