"""Phase 4A entry-page storage tests using temporary SQLite databases."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

import test_category_registry as registry_tests


class EntryPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = str(Path(self.temp.name) / "pages.db")
        self.ns = {"sqlite3": sqlite3, "DATABASE": self.database}
        exec(
            compile(registry_tests.HELPERS, str(registry_tests.SOURCE), "exec"),
            self.ns,
        )
        self.restart()

    def restart(self):
        for name in (
            "initialize_database",
            "migrate_database",
            "initialize_seed_content",
        ):
            self.call(name)

    def call(self, name, *args):
        return self.ns[name](*args)

    def sql(self, statement, parameters=()):
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                return connection.execute(statement, parameters).fetchall()
        finally:
            connection.close()

    def add_entry(self, entry_id="entry", content="Page one"):
        self.assertEqual(
            self.call("add_entry", entry_id, "Entry", "World", content),
            "success",
        )

    def test_fresh_seed_and_entry_writes_create_page_one(self):
        self.assertEqual(
            self.sql("""
                SELECT e.id, e.content, p.page_number, p.content
                FROM wiki_entries AS e
                JOIN wiki_entry_pages AS p ON p.entry_id = e.id
                ORDER BY e.id
            """),
            [
                (entry_id, content, 1, content)
                for entry_id, content in self.sql(
                    "SELECT id, content FROM wiki_entries ORDER BY id"
                )
            ],
        )
        self.add_entry("new", "New lore")
        self.assertEqual(
            [page[1:] for page in self.call("get_entry_pages", "NEW")],
            [(1, "New lore")],
        )
        self.assertEqual(
            self.call(
                "update_entry", "new", "Changed", "NPC", "Changed lore"
            ),
            "success",
        )
        self.assertEqual(
            self.sql("""
                SELECT e.content, p.content
                FROM wiki_entries AS e
                JOIN wiki_entry_pages AS p ON p.entry_id = e.id
                WHERE e.id = 'new' AND p.page_number = 1
            """),
            [("Changed lore", "Changed lore")],
        )

    def test_legacy_migration_is_lossless_idempotent_and_repairs_mirror(self):
        legacy = str(Path(self.temp.name) / "legacy.db")
        connection = sqlite3.connect(legacy)
        oversized = "x" * 4500
        with connection:
            connection.execute("""
                CREATE TABLE wiki_entries (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT
                )
            """)
            connection.execute(
                "INSERT INTO wiki_entries VALUES ('legacy','Legacy','World',?,NULL)",
                (oversized,),
            )
        connection.close()
        namespace = {"sqlite3": sqlite3, "DATABASE": legacy}
        exec(
            compile(registry_tests.HELPERS, str(registry_tests.SOURCE), "exec"),
            namespace,
        )
        namespace["initialize_database"]()
        namespace["migrate_database"]()
        connection = sqlite3.connect(legacy)
        first = connection.execute(
            "SELECT id, page_number, content FROM wiki_entry_pages"
        ).fetchone()
        connection.execute(
            "UPDATE wiki_entries SET content = 'legacy repair' WHERE id = 'legacy'"
        )
        connection.commit()
        connection.close()
        namespace["migrate_database"]()
        connection = sqlite3.connect(legacy)
        try:
            second = connection.execute(
                "SELECT id, page_number, content FROM wiki_entry_pages"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(first, (1, 1, oversized))
        self.assertEqual(second, (1, 1, "legacy repair"))

    def test_migration_failure_rolls_back_schema_and_backfill(self):
        failed = str(Path(self.temp.name) / "failed.db")
        connection = sqlite3.connect(failed)
        with connection:
            connection.execute("""
                CREATE TABLE wiki_entries (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    category TEXT NOT NULL, content TEXT NOT NULL, tags TEXT
                )
            """)
            connection.execute(
                "INSERT INTO wiki_entries VALUES ('legacy','Legacy','World','Lore',NULL)"
            )
        connection.close()
        namespace = {"sqlite3": sqlite3, "DATABASE": failed}
        exec(
            compile(registry_tests.HELPERS, str(registry_tests.SOURCE), "exec"),
            namespace,
        )
        namespace["initialize_database"]()
        connection = sqlite3.connect(failed)
        with connection:
            connection.execute("""
                CREATE TABLE wiki_entry_pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL CHECK (page_number >= 1),
                    content TEXT NOT NULL,
                    FOREIGN KEY (entry_id) REFERENCES wiki_entries(id)
                        ON DELETE CASCADE,
                    UNIQUE(entry_id, page_number)
                )
            """)
            connection.execute("""
                CREATE TRIGGER reject_page_backfill
                BEFORE INSERT ON wiki_entry_pages
                BEGIN SELECT RAISE(ABORT, 'forced migration failure'); END
            """)
        connection.close()
        with self.assertRaises(sqlite3.IntegrityError):
            namespace["migrate_database"]()
        connection = sqlite3.connect(failed)
        try:
            columns = [
                row[1] for row in connection.execute("PRAGMA table_info(wiki_entries)")
            ]
            category_table = connection.execute("""
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'wiki_categories'
            """).fetchone()
            pages = connection.execute("SELECT * FROM wiki_entry_pages").fetchall()
        finally:
            connection.close()
        self.assertNotIn("image_url", columns)
        self.assertIsNone(category_table)
        self.assertEqual(pages, [])

    def test_page_helpers_validate_read_append_and_update(self):
        self.add_entry()
        self.assertEqual(self.call("get_entry_page_count", "ENTRY"), 1)
        self.assertIsNone(self.call("get_entry_page_count", "missing"))
        self.assertEqual(self.call("add_entry_page", "entry", " Page two "), "success")
        pages = self.call("get_entry_pages", "entry")
        self.assertEqual([page[1:] for page in pages], [
            (1, "Page one"), (2, " Page two ")
        ])
        self.assertEqual(self.call("get_entry_page", "entry", 2), pages[1])
        self.assertEqual(
            self.call("update_entry_page", "entry", pages[1][0], "Changed two"),
            "success",
        )
        self.assertEqual(self.call("get_entry", "entry")[3], "Page one")
        self.assertEqual(
            self.call("update_entry_page", "entry", pages[0][0], "Changed one"),
            "success",
        )
        self.assertEqual(self.call("get_entry", "entry")[3], "Changed one")
        self.assertEqual(self.call("add_entry_page", "missing", "Lore"), "not_found")
        self.assertEqual(
            self.call("update_entry_page", "entry", 9999, "Lore"), "not_found"
        )
        self.add_entry("other", "Other lore")
        other_page_id = self.call("get_entry_pages", "other")[0][0]
        self.assertEqual(
            self.call("update_entry_page", "entry", other_page_id, "Wrong"),
            "not_found",
        )
        self.assertEqual(
            self.call("delete_entry_page", "entry", other_page_id), "not_found"
        )
        self.assertEqual(
            self.call("move_entry_page", "entry", other_page_id, 1), "not_found"
        )
        for content in (None, "", "   ", "x" * 4001):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.call("add_entry_page", "entry", content)
        for page_id in (True, 0, -1, "1"):
            with self.subTest(page_id=page_id):
                with self.assertRaises(ValueError):
                    self.call("update_entry_page", "entry", page_id, "Lore")

    def test_search_finds_all_pages_without_duplicate_entries(self):
        self.add_entry("multi", "First-page marker")
        self.call("add_entry_page", "multi", "Second-page unique marker")
        self.call("add_entry_page", "multi", "Another unique marker")

        results = self.call("search_wiki", "unique marker")

        self.assertEqual(results, [
            ("multi", "Entry", "World", "First-page marker", ""),
        ])

    def test_delete_page_compacts_order_and_mirrors_new_page_one(self):
        self.add_entry(content="One")
        self.call("add_entry_page", "entry", "Two")
        self.call("add_entry_page", "entry", "Three")
        pages = self.call("get_entry_pages", "entry")
        self.assertEqual(
            self.call("delete_entry_page", "entry", pages[0][0]), "success"
        )
        remaining = self.call("get_entry_pages", "entry")
        self.assertEqual(
            [(page[0], page[1], page[2]) for page in remaining],
            [(pages[1][0], 1, "Two"), (pages[2][0], 2, "Three")],
        )
        self.assertEqual(self.call("get_entry", "entry")[3], "Two")
        self.assertEqual(
            self.call("delete_entry_page", "entry", remaining[1][0]), "success"
        )
        self.assertEqual(
            self.call("delete_entry_page", "entry", remaining[0][0]), "only_page"
        )
        self.assertEqual(
            self.call("delete_entry_page", "entry", 9999), "not_found"
        )

    def test_move_page_preserves_ids_order_and_page_one_mirror(self):
        self.add_entry(content="One")
        self.call("add_entry_page", "entry", "Two")
        self.call("add_entry_page", "entry", "Three")
        pages = self.call("get_entry_pages", "entry")
        self.assertEqual(
            self.call("move_entry_page", "entry", pages[1][0], -1), "success"
        )
        moved = self.call("get_entry_pages", "entry")
        self.assertEqual(
            [(page[0], page[1], page[2]) for page in moved],
            [(pages[1][0], 1, "Two"), (pages[0][0], 2, "One"),
             (pages[2][0], 3, "Three")],
        )
        self.assertEqual(self.call("get_entry", "entry")[3], "Two")
        self.assertEqual(
            self.call("move_entry_page", "entry", pages[1][0], -1),
            "at_boundary",
        )
        self.assertEqual(
            self.call("move_entry_page", "entry", 9999, 1), "not_found"
        )
        for direction in (0, 2, True, "1"):
            with self.subTest(direction=direction):
                with self.assertRaises(ValueError):
                    self.call(
                        "move_entry_page", "entry", pages[1][0], direction
                    )

    def test_entry_delete_cascades_pages_and_relationships(self):
        self.add_entry("source", "One")
        self.add_entry("target", "Target")
        self.call("add_entry_page", "source", "Two")
        self.sql("""
            INSERT INTO wiki_relationships (source_id, relationship, target_id)
            VALUES ('source', 'related_to', 'target')
        """)
        self.assertTrue(self.call("delete_entry", "source"))
        self.assertEqual(
            self.sql("SELECT * FROM wiki_entry_pages WHERE entry_id = 'source'"),
            [],
        )
        self.assertEqual(self.sql("SELECT * FROM wiki_relationships"), [])
        self.assertFalse(self.call("delete_entry", "source"))

    def test_entry_and_page_write_failures_roll_back(self):
        self.sql("""
            CREATE TRIGGER reject_new_page BEFORE INSERT ON wiki_entry_pages
            WHEN NEW.entry_id = 'blocked'
            BEGIN SELECT RAISE(ABORT, 'forced page failure'); END
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.call("add_entry", "blocked", "Blocked", "World", "Lore")
        self.assertEqual(
            self.sql("SELECT * FROM wiki_entries WHERE id = 'blocked'"), []
        )
        self.add_entry()
        self.sql("""
            CREATE TRIGGER reject_page_update BEFORE UPDATE ON wiki_entry_pages
            BEGIN SELECT RAISE(ABORT, 'forced update failure'); END
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.call(
                "update_entry", "entry", "Changed", "NPC", "Changed content"
            )
        self.assertEqual(
            self.sql("SELECT title, category, content FROM wiki_entries WHERE id='entry'"),
            [("Entry", "World", "Page one")],
        )

    def test_page_reorder_failure_rolls_back_delete_and_mirror(self):
        self.add_entry(content="One")
        self.call("add_entry_page", "entry", "Two")
        self.call("add_entry_page", "entry", "Three")
        before = self.call("get_entry_pages", "entry")
        self.sql("""
            CREATE TRIGGER reject_page_renumber
            BEFORE UPDATE OF page_number ON wiki_entry_pages
            BEGIN SELECT RAISE(ABORT, 'forced reorder failure'); END
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.call("delete_entry_page", "entry", before[0][0])
        self.assertEqual(self.call("get_entry_pages", "entry"), before)
        self.assertEqual(self.call("get_entry", "entry")[3], "One")

    def test_append_holds_write_lock_while_choosing_page_number(self):
        self.add_entry()
        attempts = []

        def connect(database):
            connection = sqlite3.connect(database)

            def trace(statement):
                if "SELECT COALESCE(MAX(page_number)" in statement:
                    other = sqlite3.connect(database, timeout=0)
                    try:
                        other.execute("""
                            INSERT INTO wiki_entry_pages
                            (entry_id, page_number, content)
                            VALUES ('entry', 2, 'Racer')
                        """)
                        other.commit()
                        attempts.append("inserted")
                    except sqlite3.OperationalError as error:
                        attempts.append(str(error))
                    finally:
                        other.close()

            connection.set_trace_callback(trace)
            return connection

        self.ns["sqlite3"] = SimpleNamespace(connect=connect)
        self.assertEqual(self.call("add_entry_page", "entry", "Two"), "success")
        self.assertEqual(attempts, ["database is locked"])


if __name__ == "__main__":
    unittest.main()
