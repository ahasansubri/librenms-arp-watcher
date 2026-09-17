# Security

## Deployment controls

- Run the watcher as the unprivileged `arpwatcher` Linux account. The optional
  root-owned cycle starts as root only to invoke discovery as `librenms` and the
  watcher as `arpwatcher`; do not make its script or live config group-writable.
- Grant only SELECT on the three required LibreNMS tables; no global/database-wide writes.
- Store live configuration under `/etc/arp-watcher`, readable only by root and the service group.
- Keep `/usr/local/sbin/arp-monitor-cycle` owned by root with mode `0750` and
  its systemd units owned by root with mode `0644`.
- Protect the SQLite database and backups: they contain device names, MAC/IP mappings and history.
- Prefer SMTP with STARTTLS or implicit TLS and valid certificates. Disable TLS only
  for a deliberately approved, protected relay path; never send credentials over an untrusted cleartext connection.
- Restrict network access to the required database and SMTP services.
- Review endpoint ownership before approving a baseline. ARP can be spoofed.
- Monitor journald and SMTP delivery independently. A successful systemd execution
  does not prove that all intended recipients received email.

## Sensitive data

Never commit real `config.json`, `mysql.cnf`, `.env` files, SQLite databases,
backups, packet captures, cryptographic keys or production logs. `.gitignore`
helps prevent accidental additions; it does not remove files already tracked or
prevent `git add -f`. The bundled hygiene check is not a comprehensive secret scanner.

If a secret is published, revoke/rotate it immediately and follow the hosting
platform's procedure for removing sensitive content from history. Deleting the
current file is not sufficient.

## Report a vulnerability

Do not post exploit details, credentials or production evidence in a public issue.
If the repository owner enables GitHub private vulnerability reporting, use the
repository's Security → Report a vulnerability flow. Otherwise request a private
reporting channel from the maintainer without disclosing the sensitive details.
No response-time or support commitment is implied by this repository.

## Scope

This tool provides observation-based alerts only. It is not a security boundary,
device identity authority, NAC system, intrusion prevention system or proof of
network inventory completeness. Review README limitations before production use.
