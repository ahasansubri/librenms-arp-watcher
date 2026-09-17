"""Tests for publication checks using only synthetic sensitive-looking fixtures."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_repository", ROOT / "scripts/check_repository.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class RepositoryChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "examples").mkdir()
        for name in ["config.json.example", "mysql.cnf.example"]:
            (self.root / "examples" / name).write_bytes((ROOT / "examples" / name).read_bytes())
        for relative in [
            "scripts/arp-monitor-cycle",
            "deploy/systemd/arp-monitor-cycle.service",
            "deploy/systemd/arp-monitor-cycle.timer",
            "docs/ARP_REFRESH_CYCLE.md",
        ]:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())

    def test_clean_examples(self):
        self.assertEqual(checker.check(self.root), [])

    def test_reject_live_config(self):
        (self.root / "config.json").write_text("{}")
        self.assertTrue(any("Sensitive runtime" in e for e in checker.check(self.root)))

    def test_reject_private_key_marker(self):
        (self.root / "accidental.txt").write_text("-----BEGIN " + "PRIVATE KEY-----")
        self.assertTrue(any("Potential credential" in e for e in checker.check(self.root)))

    def test_reject_broken_doc_link(self):
        (self.root / "README.md").write_text("[Missing](missing.md)")
        self.assertTrue(any("Broken relative link" in e for e in checker.check(self.root)))

    def test_ignore_git_metadata(self):
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config.json").write_text("{}")
        self.assertEqual(checker.check(self.root), [])

    def test_reject_missing_refresh_cycle_file(self):
        (self.root / "deploy/systemd/arp-monitor-cycle.timer").unlink()
        self.assertTrue(any("Required refresh-cycle" in e for e in checker.check(self.root)))


if __name__ == "__main__":
    unittest.main()
