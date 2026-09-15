"""Database tests without importing bot.py (which starts Discord at import).

Extract only explicitly named function definitions, using the real schema
initializers against a temporary database. No .env or real wiki.db is read.
Run: python -m unittest discover -s tests -v
"""
import ast
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace


FUNCTIONS = {
    "initialize_database", "migrate_database", "initialize_seed_content",
    "_validate_entry_page_content", "_validate_entry_page_id",
    "_sync_entry_page_one", "get_entry_pages", "get_entry_page",
    "get_entry_page_count", "add_entry_page", "update_entry_page",
    "delete_entry_page", "move_entry_page", "get_entry", "delete_entry",
    "get_category", "category_exists", "get_category_entry_count",
    "_validate_category_fields", "create_category", "update_category",
    "delete_category", "get_categories", "get_category_icon",
    "get_category_entries", "add_entry", "update_entry",
}
SOURCE = Path(__file__).resolve().parents[1] / "bot.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
HELPERS = ast.Module(
    body=[node for node in TREE.body
          if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS],
    type_ignores=[],
)
assert {node.name for node in HELPERS.body} == FUNCTIONS


class CategoryRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = str(Path(self.temp.name) / "test.db")
        self.ns = {"sqlite3": sqlite3, "DATABASE": self.database}
        exec(compile(HELPERS, str(SOURCE), "exec"), self.ns)
        for name in ("initialize_database", "migrate_database", "initialize_seed_content"):
            self.ns[name]()
        self.sql("DELETE FROM wiki_entry_pages")
        self.sql("DELETE FROM wiki_entries")

    def call(self, name, *args, **kwargs):
        return self.ns[name](*args, **kwargs)

    def sql(self, statement, parameters=()):
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                return connection.execute(statement, parameters).fetchall()
        finally:
            connection.close()

    def entry(self, entry_id, category):
        self.sql("""INSERT INTO wiki_entries
            (id, title, category, content, tags, image_url)
            VALUES (?, 'Title', ?, 'Lore', 'tag', 'https://example.com/image.png')
        """, (entry_id, category))
        self.sql("""INSERT INTO wiki_entry_pages
            (entry_id, page_number, content) VALUES (?, 1, 'Lore')
        """, (entry_id,))

    def test_details_empty_and_missing(self):
        row = self.call("get_category", " bEsTiArY ")
        self.assertEqual(row[1:], ("Bestiary", "Creatures and monsters encountered in the world.", "🐉", 50))
        self.assertTrue(self.call("category_exists", "Bestiary"))
        self.assertEqual(self.call("get_category_entry_count", "Bestiary"), 0)
        self.assertIsNone(self.call("get_category", "Missing"))
        self.assertFalse(self.call("category_exists", "Missing"))
        self.assertIsNone(self.call("get_category_entry_count", "Missing"))

    def test_create_duplicate_order_and_fallback(self):
        self.assertEqual(self.call("create_category", " Zeta ", sort_order=15), "success")
        self.assertEqual(self.call("create_category", "alpha", sort_order=15), "success")
        self.assertEqual(self.call("create_category", " ALPHA "), "duplicate")
        self.assertEqual(self.call("get_categories")[:4], ["World", "alpha", "Zeta", "Location"])
        self.assertEqual(self.call("get_category_icon", "Zeta"), "📚")
        self.assertEqual(self.call("get_category_icon", "unknown"), "📚")
        self.assertEqual(self.call("get_category", "Zeta")[2:], (None, None, 15))

    def test_entry_writes_require_and_canonicalize_registered_categories(self):
        self.assertEqual(self.call(
            "add_entry", "one", "One", " world ", "Lore", "tag"
        ), "success")
        self.assertEqual(
            self.sql("SELECT category FROM wiki_entries WHERE id = 'one'"),
            [("World",)]
        )
        self.assertEqual(self.call(
            "add_entry", "missing", "Missing", "Unknown", "Lore"
        ), "invalid_category")
        self.assertEqual(self.call(
            "add_entry", "one", "Duplicate", "World", "Other"
        ), "duplicate")

        self.assertEqual(self.call(
            "update_entry", "one", "Changed", "Unknown", "Changed", "new"
        ), "invalid_category")
        self.assertEqual(
            self.sql("SELECT title, category FROM wiki_entries WHERE id = 'one'"),
            [("One", "World")]
        )
        self.assertEqual(self.call(
            "update_entry", "one", "Changed", " npc ", "Changed", "new"
        ), "success")
        self.assertEqual(
            self.sql("SELECT title, category FROM wiki_entries WHERE id = 'one'"),
            [("Changed", "NPC")]
        )
        self.assertEqual(self.call(
            "update_entry", "missing", "Missing", "World", "Lore"
        ), "not_found")

    def test_invalid_fields_do_not_write(self):
        original = self.sql("SELECT * FROM wiki_categories")
        cases = [(None, None, None, 0), (" ", None, None, 0),
                 ("x" * 51, None, None, 0), ("Valid", 123, None, 0),
                 ("Valid", None, 123, 0), ("Valid", None, None, True),
                 ("Valid", None, None, "10"), ("Valid", None, None, 1.5),
                 ("Valid", None, None, 2 ** 63)]
        for fields in cases:
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    self.call("create_category", *fields)
                with self.assertRaises(ValueError):
                    self.call("update_category", "World", *fields)
        self.assertEqual(self.sql("SELECT * FROM wiki_categories"), original)

    def test_rename_preserves_content_images_relationships_and_id(self):
        self.entry("one", "World")
        self.entry("two", "world")
        self.entry("other", "NPC")
        self.sql("INSERT INTO wiki_relationships (source_id, relationship, target_id) VALUES ('one', 'related_to', 'two')")
        before = self.sql("SELECT id,title,content,tags,image_url FROM wiki_entries ORDER BY id")
        links = self.sql("SELECT * FROM wiki_relationships")
        category_id = self.call("get_category", "World")[0]
        self.assertEqual(self.call("update_category", " WORLD ", " Lore ", "New description", "📖", 7), "success")
        self.assertEqual(self.call("get_category", "Lore"), (category_id, "Lore", "New description", "📖", 7))
        self.assertFalse(self.call("category_exists", "World"))
        self.assertEqual(self.call("get_category_entry_count", "lore"), 2)
        self.assertEqual(len(self.call("get_category_entries", "Lore")), 2)
        self.assertEqual(self.sql("SELECT id,title,content,tags,image_url FROM wiki_entries ORDER BY id"), before)
        self.assertEqual(self.sql("SELECT * FROM wiki_relationships"), links)
        self.assertEqual(self.sql("SELECT category FROM wiki_entries WHERE id='other'"), [("NPC",)])

    def test_case_only_rename_and_clear_optional_fields(self):
        self.entry("one", "WORLD")
        self.assertEqual(self.call("update_category", "world", "WORLD", None, None, -1), "success")
        self.assertEqual(self.call("get_category", "world")[1:], ("WORLD", None, None, -1))
        self.assertEqual(len(self.call("get_category_entries", "WORLD")), 1)

    def test_duplicate_rename_and_missing_update_leave_data_unchanged(self):
        self.entry("one", "World")
        categories = self.sql("SELECT * FROM wiki_categories")
        entries = self.sql("SELECT * FROM wiki_entries")
        self.assertEqual(self.call("update_category", "World", " npc ", None, None, 0), "duplicate")
        self.assertEqual(self.call("update_category", "Missing", "New", None, None, 0), "not_found")
        self.assertEqual(self.sql("SELECT * FROM wiki_categories"), categories)
        self.assertEqual(self.sql("SELECT * FROM wiki_entries"), entries)

    def test_rename_failure_rolls_back_registry_and_entries(self):
        self.entry("one", "World")
        self.sql("""CREATE TRIGGER reject_rename BEFORE UPDATE OF category ON wiki_entries
            BEGIN SELECT RAISE(ABORT, 'forced rename failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.call("update_category", "World", "Lore", None, None, 0)
        self.assertTrue(self.call("category_exists", "World"))
        self.assertFalse(self.call("category_exists", "Lore"))
        self.assertEqual(self.sql("SELECT category FROM wiki_entries"), [("World",)])
        self.assertEqual(self.call("create_category", "After failure"), "success")

    def test_delete_guards_case_variants_and_preserves_entries(self):
        self.entry("one", "bestiary")
        self.assertEqual(self.call("get_category_entry_count", " BESTIARY "), 1)
        entries = self.sql("SELECT * FROM wiki_entries")
        self.assertEqual(self.call("delete_category", " Bestiary "), "in_use")
        self.assertTrue(self.call("category_exists", "Bestiary"))
        self.assertEqual(self.sql("SELECT * FROM wiki_entries"), entries)
        self.assertEqual(self.call("delete_category", "Missing"), "not_found")

    def test_delete_empty_and_persistence(self):
        self.call("create_category", "Custom")
        self.assertEqual(self.call("delete_category", " CUSTOM "), "success")
        self.assertEqual(self.call("delete_category", "Custom"), "not_found")
        self.assertNotIn("Custom", self.call("get_categories"))
        self.call("initialize_seed_content")
        self.assertFalse(self.call("category_exists", "Custom"))
        self.assertEqual(self.sql("PRAGMA integrity_check"), [("ok",)])

    def test_delete_failure_rolls_back_and_releases_lock(self):
        self.sql("""CREATE TRIGGER reject_delete BEFORE DELETE ON wiki_categories
            BEGIN SELECT RAISE(ABORT, 'forced delete failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.call("delete_category", "Bestiary")
        self.assertTrue(self.call("category_exists", "Bestiary"))
        self.assertEqual(self.call("create_category", "After failure"), "success")

    def test_delete_holds_write_lock_during_usage_check(self):
        attempts = []
        def connect(database):
            connection = sqlite3.connect(database)
            def trace(statement):
                if "SELECT 1 FROM wiki_entries" in statement:
                    other = sqlite3.connect(database, timeout=0)
                    try:
                        other.execute("INSERT INTO wiki_entries (id,title,category,content) VALUES ('racer','Racer','Bestiary','Lore')")
                        other.commit()
                        attempts.append("inserted")
                    except sqlite3.OperationalError as error:
                        attempts.append(str(error))
                    finally:
                        other.close()
            connection.set_trace_callback(trace)
            return connection
        self.ns["sqlite3"] = SimpleNamespace(connect=connect)
        self.assertEqual(self.call("delete_category", "Bestiary"), "success")
        self.assertEqual(attempts, ["database is locked"])


if __name__ == "__main__":
    unittest.main()
