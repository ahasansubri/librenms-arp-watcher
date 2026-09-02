# Publish this source package to GitHub

Suggested repository name: `librenms-arp-watcher`

Suggested description:

> Interface-scoped ARP baseline monitoring for LibreNMS with explicit approvals, SQLite state and HTML email alerts.

Suggested topics: `librenms`, `arp`, `network-monitoring`, `python`, `snmp`,
`soc`, `network-security`, `systemd`.

This package has not been pushed to any account or repository.

## Before publication

1. Confirm you have the right to publish the source and that no organizational
   approval is outstanding. Sanitized examples do not establish ownership rights.
2. Choose a license deliberately. MIT is one option for permissive reuse, but no
   license grant or copyright owner has been assumed in this package. Add LICENSE
   and update the README license section after making that decision.
3. Run tests and repository checks on Linux:

   ```bash
   python3 -m unittest discover -s tests -v
   python3 scripts/check_repository.py
   ```

4. Review every file manually and run your preferred secret scanner. Confirm no
   live configs, keys, real IP/MAC inventories, captured emails or SQLite state
   are included. The provided check is intentionally limited, not a certification.

## New repository

Extract the ZIP and enter the `librenms-arp-watcher` directory. Create an empty
GitHub repository with the desired visibility. Do not auto-create a README if
you want to use the commands below without merging remote initial content.

Then run locally, replacing the repository URL with your own:

```bash
git init -b main
git add .
git diff --cached --check
git diff --cached --stat
git diff --cached
```

Review the complete staged diff. Then:

```bash
git commit -m "Initial release: LibreNMS ARP baseline watcher"
git remote add origin https://github.com/YOUR_USERNAME/librenms-arp-watcher.git
git push -u origin main
```

Use your normal GitHub authentication; never put access tokens in remote URLs.
The ZIP includes `.github`, `.gitignore`, `.gitattributes` and `.editorconfig`.
Copy the complete extracted folder so these repository files are not omitted.

## Existing repository

Work on a branch in your existing clone. Copy the source into an appropriate
project folder, preserving unrelated files. Review any naming conflicts before
overwriting. Put the workflow under the repository-root `.github/workflows/`
and update its command working directory if the project is in a subfolder.
Do not run `git init`, overwrite your remote, or force-push an existing project.

## After pushing

- Check the Actions tab; local tests do not prove the GitHub matrix has passed.
- Review workflow permissions and dependency/action update policy. The supplied
  workflow uses major-version action tags; pin reviewed commit SHAs if your
  repository's supply-chain policy requires immutable references.
- Enable secret scanning/push protection where available.
- Enable private vulnerability reporting if you will support it.
- Configure branch protection and required checks as appropriate.
- Add sanitized screenshots only after inspecting all visible text and metadata.
- After validation and license selection, create a release if desired. The
  package's version string is `0.1.0`; no tag or GitHub release has been created.
