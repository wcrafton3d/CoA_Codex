"""Restart and adoption tests; all writes stay in temporary databases."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import test_category_registry as registry_tests


class StartupSeedingTests(unittest.TestCase):
    sql = registry_tests.CategoryRegistryTests.sql
    call = registry_tests.CategoryRegistryTests.call
    entry = registry_tests.CategoryRegistryTests.entry

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = str(Path(self.temp.name) / "startup.db")
        self.ns = {"sqlite3": sqlite3, "DATABASE": self.database}
        exec(compile(registry_tests.HELPERS, str(registry_tests.SOURCE), "exec"), self.ns)

    def restart(self):
        for name in ("initialize_database", "migrate_database", "initialize_seed_content"):
            self.call(name)

    def snapshot(self):
        return [self.sql("SELECT * FROM " + table + " ORDER BY id")
                for table in ("wiki_entries", "wiki_categories",
                              "wiki_relationships", "wiki_entry_pages")]

    def test_fresh_database_seeds_once(self):
        self.restart()
        self.assertEqual(len(self.call("get_categories")), 5)
        self.assertEqual(self.sql("SELECT id FROM wiki_entries ORDER BY id"),
                         [("aldren",), ("blackwood",), ("history",), ("iron-covenant",), ("setting",)])
        self.assertEqual(self.sql("SELECT state FROM wiki_bootstrap"), [("complete",)])
        before = self.snapshot()
        self.restart()
        self.assertEqual(self.snapshot(), before)

    def test_renames_deletions_and_edits_survive_restart(self):
        self.restart()
        self.assertEqual(self.call("update_category", "World", "Lore", "Custom", "📖", 1), "success")
        self.assertEqual(self.call("delete_category", "Bestiary"), "success")
        self.sql("DELETE FROM wiki_entries WHERE id='aldren'")
        self.assertEqual(self.call("delete_category", "NPC"), "success")
        self.assertEqual(self.call(
            "update_entry", "setting", "The Setting", "Lore", "Edited",
            "world, overview, setting", "https://example.com/a.png"
        ), "success")
        self.sql("INSERT INTO wiki_relationships (source_id,relationship,target_id) VALUES ('setting','related_to','history')")
        before = self.snapshot()
        self.restart()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.call("category_exists", "World"))
        self.assertFalse(self.call("category_exists", "Bestiary"))
        self.assertFalse(self.call("category_exists", "NPC"))

    def test_existing_installation_adopted_without_restoring_missing_defaults(self):
        self.restart()
        self.sql("DROP TABLE wiki_bootstrap")
        self.call("update_category", "World", "Lore", None, None, 0)
        self.call("delete_category", "Bestiary")
        self.sql("DELETE FROM wiki_entries WHERE id='aldren'")
        before = self.snapshot()
        self.restart()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.sql("SELECT state FROM wiki_bootstrap"), [("complete",)])

    def test_existing_empty_installation_stays_empty(self):
        self.restart()
        self.sql("DELETE FROM wiki_entries")
        self.sql("DELETE FROM wiki_categories")
        self.sql("DROP TABLE wiki_bootstrap")
        self.restart()
        self.assertEqual(self.snapshot(), [[], [], [], []])

    def test_clearing_all_content_after_bootstrap_does_not_reseed(self):
        self.restart()
        self.sql("DELETE FROM wiki_entries")
        self.sql("DELETE FROM wiki_categories")
        self.restart()
        self.assertEqual(self.snapshot(), [[], [], [], []])

    def test_interruption_before_migration_retries_first_run(self):
        self.call("initialize_database")
        self.assertEqual(self.sql("SELECT state FROM wiki_bootstrap"), [("pending",)])
        self.restart()
        self.assertEqual(len(self.call("get_categories")), 5)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM wiki_entries"), [(5,)])

    def test_seed_failure_rolls_back_categories_entries_and_marker(self):
        self.call("initialize_database")
        self.call("migrate_database")
        self.sql("""CREATE TRIGGER reject_seed BEFORE INSERT ON wiki_entries
            WHEN NEW.id = 'aldren'
            BEGIN SELECT RAISE(ABORT, 'forced failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.call("initialize_seed_content")
        self.assertEqual(self.snapshot(), [[], [], [], []])
        self.assertEqual(self.sql("SELECT state FROM wiki_bootstrap"), [("pending",)])
        self.sql("DROP TRIGGER reject_seed")
        self.restart()
        self.assertEqual(self.sql("SELECT COUNT(*) FROM wiki_entries"), [(5,)])
        self.assertEqual(self.sql("SELECT state FROM wiki_bootstrap"), [("complete",)])

    def test_legacy_entry_schema_is_migrated_without_new_content(self):
        self.sql("""CREATE TABLE wiki_entries (id TEXT PRIMARY KEY,
            title TEXT NOT NULL, category TEXT NOT NULL,
            content TEXT NOT NULL, tags TEXT)""")
        self.sql("INSERT INTO wiki_entries VALUES ('legacy','Legacy','Custom','Lore','tag')")
        self.restart()
        self.assertEqual(self.sql("SELECT * FROM wiki_entries"),
                         [("legacy", "Legacy", "Custom", "Lore", "tag", None)])
        self.assertEqual(self.call("get_categories"), [])
        self.assertEqual(self.sql("PRAGMA integrity_check"), [("ok",)])


if __name__ == "__main__":
    unittest.main()
