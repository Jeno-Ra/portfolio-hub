"""独立运行：python backend/tests/test_portable_backup.py，不读取用户配置或数据。"""
import logging
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

# Only the portable module is imported; its config dependency is stubbed per test.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from desktop import portable


class PortableBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "data.db"
        self.target = self.root / "data.db.bak-20260101"
        self.config = types.ModuleType("backend.app.config")
        self.config.settings = types.SimpleNamespace(db_path=str(self.db))
        self.modules = patch.dict(sys.modules, {"backend.app.config": self.config})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.clock = patch.object(portable.time, "strftime", return_value="20260101")
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def assert_no_temps(self):
        self.assertEqual(list(self.root.glob(".*.backup-*.tmp")), [])

    def test_online_backup_includes_uncheckpointed_wal_not_other_files(self):
        with closing(sqlite3.connect(self.db)) as source:
            source.execute("PRAGMA journal_mode=WAL")
            source.execute("PRAGMA wal_autocheckpoint=0")
            source.execute("CREATE TABLE records (value TEXT)")
            source.execute("INSERT INTO records VALUES ('wal data')")
            source.commit()
            self.assertTrue(Path(str(self.db) + "-wal").exists())
            (self.root / "upload.txt").write_text("not included")
            portable.backup_database()
            with closing(sqlite3.connect(self.target)) as backup:
                self.assertEqual(backup.execute("SELECT value FROM records").fetchall(), [("wal data",)])
                self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        self.assert_no_temps()
        self.assertFalse((self.root / "upload.txt.bak-20260101").exists())

    def test_existing_backup_and_old_backups_are_never_changed(self):
        self.db.write_bytes(b"invalid source")
        self.target.write_bytes(b"existing")
        old = [self.root / f"data.db.bak-2025010{i}" for i in range(1, 5)]
        for path in old:
            path.write_bytes(b"old")
        portable.backup_database()
        self.assertEqual(self.target.read_bytes(), b"existing")
        self.assertTrue(all(path.read_bytes() == b"old" for path in old))
        self.assert_no_temps()

    def test_failure_does_not_publish_or_delete_old_backup(self):
        self.db.write_bytes(b"not sqlite")
        old = self.root / "data.db.bak-20250101"
        old.write_bytes(b"old")
        with self.assertLogs(level=logging.ERROR):
            portable.backup_database()
        self.assertFalse(self.target.exists())
        self.assertEqual(old.read_bytes(), b"old")
        self.assert_no_temps()

    def test_publish_race_does_not_overwrite_existing_backup(self):
        with closing(sqlite3.connect(self.db)) as source:
            source.execute("CREATE TABLE records (value TEXT)")
        old = self.root / "data.db.bak-20250101"
        old.write_bytes(b"old")
        def publish_race(src, dst):
            Path(dst).write_bytes(b"concurrent backup")
            raise FileExistsError("concurrent publication")
        with patch.object(portable.os, "link", side_effect=publish_race), self.assertLogs(level=logging.ERROR):
            portable.backup_database()
        self.assertEqual(self.target.read_bytes(), b"concurrent backup")
        self.assertEqual(old.read_bytes(), b"old")
        self.assert_no_temps()

    def test_missing_database_is_noop(self):
        portable.backup_database()
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
