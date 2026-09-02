"""Offline regression tests. No production database or email connections."""
import contextlib
import io
import json
import re
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import arp_watcher as w


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = self.config({"monitored_interfaces": {"6": ["internal1"], "10": ["internal2"]}})
        self.db = w.connect_state(self.cfg["state_db"])
        self.addCleanup(self.db.close)

    def config(self, scope):
        data = {"mysql_defaults_file": "/unused", "state_db": str(self.root / "state.sqlite3"),
                "lock_file": str(self.root / "watcher.lock"), **scope}
        path = self.root / "config.json"
        path.write_text(json.dumps(data))
        return w.load_config(str(path))

    def entry(self, device=10, interface="internal2", mac="aabbccddeeff", ip="192.0.2.20"):
        return w.ArpEntry(device, "192.0.2.1", "test-site", 100, interface, ip, mac)

    def approve(self, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            w.approve_mapping(self.db, self.cfg, *args, **kwargs)

    def test_new_config(self):
        self.assertEqual(self.cfg["monitored_interfaces"], {6: ["internal1"], 10: ["internal2"]})

    def test_legacy_config(self):
        cfg = self.config({"device_ids": [6, 7, 8, 9], "interface": "internal1"})
        self.assertEqual(cfg["monitored_interfaces"][9], ["internal1"])

    def test_invalid_configs(self):
        for scopes in [
            {"monitored_interfaces": {}},
            {"monitored_interfaces": {"10": "internal2"}},
            {"monitored_interfaces": {"10": []}},
            {"monitored_interfaces": {"10": [""]}},
            {"monitored_interfaces": {"10 OR 1=1": ["inside"]}},
            {"monitored_interfaces": {"0": ["inside"]}},
            {"monitored_interfaces": {"10": ["inside"], "010": ["outside"]}},
            {"monitored_interfaces": {"10": ["inside"]}, "interface": "internal1"},
        ]:
            with self.subTest(scopes=scopes), self.assertRaises(ValueError):
                self.config(scopes)

    def test_query_scope_pairing(self):
        # Execute the selection offline after translating MySQL hex strings to SQL strings.
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript("""
        CREATE TABLE devices(device_id, hostname, sysName);
        CREATE TABLE ports(device_id, port_id, ifName);
        CREATE TABLE ipv4_mac(device_id, port_id, ipv4_address, mac_address);
        INSERT INTO devices VALUES (6,'192.0.2.6','old'), (10,'192.0.2.10','new');
        INSERT INTO ports VALUES (6,61,'internal1'),(6,62,'internal2'),
                                 (10,101,'internal1'),(10,102,'internal2');
        INSERT INTO ipv4_mac VALUES (6,61,'192.0.2.61','aabbccddee61'),
          (6,62,'192.0.2.62','aabbccddee62'),(10,101,'192.0.2.101','aabbccdde101'),
          (10,102,'192.0.2.102','aabbccdde102');
        """)
        def mysql(command, **kwargs):
            self.assertTrue(command[1].startswith("--defaults-extra-file="))
            sql = re.sub(r"0x([0-9a-f]+)", lambda m: "'" + bytes.fromhex(m[1]).decode().replace("'", "''") + "'", command[-1])
            rows = db.execute(sql).fetchall()
            return subprocess.CompletedProcess(command, 0, "\n".join("\t".join(map(str,r)) for r in rows), "")
        with patch.object(w.subprocess, "run", side_effect=mysql):
            entries = w.query_current_arp(self.cfg)
        self.assertEqual({(e.device_id,e.if_name) for e in entries}, {(6,"internal1"),(10,"internal2")})

    def test_special_interface_names_safe(self):
        self.cfg["monitored_interfaces"] = {10: ["inside'\\test"]}
        with patch.object(w.subprocess, "run", return_value=subprocess.CompletedProcess([],0,"","")) as run:
            with self.assertLogs(level="WARNING"):
                w.query_current_arp(self.cfg)
        self.assertIn("0x" + "inside'\\test".encode().hex(), run.call_args.args[0][-1])

    def test_mysql_failure(self):
        with patch.object(w.subprocess, "run", return_value=subprocess.CompletedProcess([],1,"","test failure")):
            with self.assertRaises(RuntimeError):
                w.query_current_arp(self.cfg)

    def test_approval_preserves_baseline(self):
        with contextlib.redirect_stdout(io.StringIO()):
            w.initialize_baseline(self.db, [self.entry(6,"internal1", "001122334455")])
        self.approve(10, "aa:bb:cc:dd:ee:ff", "192.0.2.20", "new", interface="internal2")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM approved").fetchone()[0], 2)
        self.assertEqual(w.classify(self.db, [self.entry()]), [])
        with self.assertRaises(RuntimeError):
            w.initialize_baseline(self.db, [self.entry()])

    def test_approval_infers_single_interface(self):
        self.approve(10,"aabbccddeeff","192.0.2.20","")
        self.assertEqual(self.db.execute("SELECT if_name FROM approved").fetchone()[0], "internal2")

    def test_approval_rejects_unmonitored_scope(self):
        for device, name in [(10,"internal1"),(99,"internal2")]:
            with self.assertRaises(ValueError):
                self.approve(device,"aabbccddeeff","192.0.2.20","",interface=name)

    def test_multiple_interface_approval(self):
        self.cfg["monitored_interfaces"][10] = ["internal1","internal2"]
        with self.assertRaises(ValueError):
            self.approve(10,"aabbccddeeff","192.0.2.20","")
        self.approve(10,"aabbccddeeff","192.0.2.20","",interface="internal2")

    def test_classification(self):
        self.assertEqual(w.classify(self.db,[self.entry()])[0].event_type,"UNKNOWN_MAC")
        self.approve(10,"aabbccddeeff","192.0.2.20","")
        self.assertEqual(w.classify(self.db,[self.entry(ip="192.0.2.21")])[0].event_type,"KNOWN_MAC_NEW_IP")
        self.assertEqual(w.classify(self.db,[self.entry(6,"internal1")])[0].event_type,"MAC_MOVED")

    def test_dry_run_no_alert_writes_or_mail(self):
        with patch.object(w,"send_email") as send, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(w.process_run(self.db,self.cfg,[self.entry()],True),2)
        send.assert_not_called()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0],0)

    def test_notification_and_recovery(self):
        with patch.object(w,"send_email") as send:
            w.process_run(self.db,self.cfg,[self.entry()],False)
            w.process_run(self.db,self.cfg,[self.entry()],False)
            self.assertEqual(send.call_count,1)
            w.process_run(self.db,self.cfg,[],False)
            self.assertEqual(send.call_count,1)
            w.process_run(self.db,self.cfg,[],False)
            self.assertEqual(send.call_count,2)
            self.assertIn("[RECOVERED]",send.call_args.args[1])

    def test_removed_scope_does_not_recover(self):
        with patch.object(w,"send_email") as send:
            w.process_run(self.db,self.cfg,[self.entry()],False)
            del self.cfg["monitored_interfaces"][10]
            w.process_run(self.db,self.cfg,[],False)
            w.process_run(self.db,self.cfg,[],False)
            self.assertEqual(send.call_count,1)
        row = self.db.execute("SELECT active,missing_runs FROM alerts").fetchone()
        self.assertEqual(tuple(row),(1,0))

    def test_validate_config_without_database(self):
        with patch.object(w.sys,"argv",["arp_watcher.py","--config",str(self.root/"config.json"),"--validate-config"]):
            with patch.object(w,"connect_state") as connect, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(w.main(),0)
                connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
