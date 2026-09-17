import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "arp-monitor-cycle"


class MonitorCycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.log = self.root / "calls.log"
        self.lnms = self._executable("lnms", "#!/bin/sh\nexit 0\n")
        self.watcher = self._executable("arp_watcher.py", "#!/bin/sh\nexit 0\n")
        self.runuser = self._executable(
            "runuser",
            """#!/bin/sh
printf '%s\\n' "$*" >> "$CALL_LOG"
case "$*" in
  *"device:discover $FAIL_DEVICE "*) exit 17 ;;
esac
exit 0
""",
        )

    def _executable(self, name, content):
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path

    def _run(self, config, fail_device="never"):
        self.config.write_text(json.dumps(config), encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "ARP_WATCHER_CONFIG": str(self.config),
                "LIBRENMS_CLI": str(self.lnms),
                "ARP_WATCHER_PROGRAM": str(self.watcher),
                "RUNUSER_PROGRAM": str(self.runuser),
                "CALL_LOG": str(self.log),
                "FAIL_DEVICE": str(fail_device),
            }
        )
        return subprocess.run(
            ["bash", str(SCRIPT)],
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )

    def _calls(self):
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def test_refreshes_sorted_devices_then_runs_watcher(self):
        result = self._run(
            {"monitored_interfaces": {"9": ["inside"], "6": ["lan"]}}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self._calls()
        self.assertIn("-u librenms --", calls[0])
        self.assertIn("device:discover 6 -m arp-table", calls[0])
        self.assertIn("device:discover 9 -m arp-table", calls[1])
        self.assertIn("-u arpwatcher --", calls[2])
        self.assertIn("--config", calls[2])

    def test_supports_legacy_device_ids_and_removes_duplicates(self):
        result = self._run({"device_ids": [8, "7", 8]})
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self._calls()
        self.assertEqual(sum("device:discover 7" in call for call in calls), 1)
        self.assertEqual(sum("device:discover 8" in call for call in calls), 1)
        self.assertEqual(sum("-u arpwatcher --" in call for call in calls), 1)

    def test_discovery_failure_prevents_watcher(self):
        result = self._run(
            {"monitored_interfaces": {"6": ["lan"], "7": ["inside"]}},
            fail_device=7,
        )
        self.assertEqual(result.returncode, 17)
        calls = self._calls()
        self.assertTrue(any("device:discover 7" in call for call in calls))
        self.assertFalse(any("-u arpwatcher --" in call for call in calls))

    def test_rejects_invalid_device_id_before_commands(self):
        result = self._run({"monitored_interfaces": {"not-an-id": ["inside"]}})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid LibreNMS device ID", result.stderr)
        self.assertEqual(self._calls(), [])

    def test_rejects_empty_scope(self):
        result = self._run({"monitored_interfaces": {}})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be a non-empty JSON object", result.stderr)
        self.assertEqual(self._calls(), [])


if __name__ == "__main__":
    unittest.main()
