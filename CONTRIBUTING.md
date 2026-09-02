# Contributing

Before contributing, confirm the maintainer has selected a license and contribution policy.

## Development

Use Linux and Python 3.10+. No third-party Python packages are needed to run tests.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m unittest discover -s tests -v
python scripts/check_repository.py
```

Keep fixtures synthetic: use documentation addresses (`192.0.2.0/24`,
`198.51.100.0/24`, `203.0.113.0/24`), `example.com` and invented locally
administered MACs. Never attach production SQLite databases or live config.

For behavior changes, add regression tests and update documentation. Preserve
existing baselines and event keys unless the change includes a reviewed migration.
Tests must not require real SMTP, MariaDB or network device access.

## Pull request checklist

- [ ] Tests, compile check and repository hygiene checks pass.
- [ ] No credentials, production inventory or customer data are included.
- [ ] Configuration compatibility and upgrade impact are documented.
- [ ] Notification/recovery changes have active, recovery and error tests.
- [ ] Security-sensitive reports follow SECURITY.md instead of public issues.

Use a focused branch and explain the problem, change and evidence. Do not submit
generated caches, database files, local environment directories or test captures.
