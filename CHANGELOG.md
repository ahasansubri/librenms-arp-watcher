# Changelog

## 0.2.0 — Five-minute LibreNMS ARP refresh cycle

- Added a combined orchestration script that reads configured device IDs,
  refreshes only LibreNMS `arp-table` discovery, then runs the watcher.
- Discovery runs as `librenms`; baseline comparison continues as `arpwatcher`.
- Any device discovery failure stops the cycle before anomaly evaluation.
- Added exact five-minute systemd scheduling and an additional `flock` guard.
- Retained the standalone watcher service/timer for manual and legacy operation.
- Added beginner-focused installation, migration, validation and troubleshooting
  documentation for the combined cycle.

## 0.1.0 — Prepared initial public-source release

- Per-device and multi-interface selection with legacy configuration compatibility.
- SQLite approved baseline and active/recovery alert tracking.
- Explicit scope-aware approval, baseline listing, dry-run and config validation.
- HTML SMTP notifications for unknown MACs, changed IPs and cross-scope observations.
- Five-minute systemd timer and least-privilege deployment examples.
- Neutral branding and synthetic public configuration examples.
- Offline regression tests, repository hygiene checks and GitHub Actions configuration.
- Installation, operations, upgrade, security and publication documentation.

This describes the prepared source package, not an already-published GitHub release.
