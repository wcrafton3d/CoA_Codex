"""Corunum map navigation without importing or starting the bot."""

import ast
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "bot.py"
LOCATIONS = json.loads(
    (ROOT / "static/corunum-map-locations.json").read_text(encoding="utf-8")
)
NAMES = {
    "make_map_state", "CorunumMapView", "display_wiki_map", "get_map_entry"
}
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
nodes = [
    node for node in tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    and node.name in NAMES
]
assert {node.name for node in nodes} == NAMES
MODULE = ast.Module(body=nodes, type_ignores=[])


def interaction(user_id=7):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            is_done=Mock(return_value=False),
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
        ),
        edit_original_response=AsyncMock(),
    )


class CorunumMapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ns = {
            "discord": discord,
            "CORUNUM_MAP_URL": "https://example.com/corunum-map-pinned.jpg",
            "CORUNUM_MAP_LOCATIONS": LOCATIONS,
            "get_map_entry": Mock(return_value=None),
            "display_wiki_entry": AsyncMock(),
            "display_codex_home": AsyncMock(),
            "navigate_to_state": AsyncMock(),
        }
        exec(compile(MODULE, str(SOURCE), "exec"), self.ns)
        self.ns["get_map_entry"] = Mock(return_value=None)

    async def test_all_documented_pins_have_unique_picker_options(self):
        self.assertEqual(len(LOCATIONS), 24)
        self.assertEqual(
            [item["number"] for item in LOCATIONS], list(range(1, 25))
        )
        self.assertEqual(LOCATIONS[0]["name"], "Adlucem")
        i = interaction()
        await self.ns["display_wiki_map"](i)
        call = i.response.send_message.call_args
        self.assertEqual(call.kwargs["embed"].image.url,
                         self.ns["CORUNUM_MAP_URL"])
        view = call.kwargs["view"]
        self.assertEqual(len(view.location_select.options), 24)

    async def test_missing_entry_leaves_map_open(self):
        i = interaction()
        view = self.ns["CorunumMapView"](7)
        view.location_select._values = ["13"]
        await view.select_location(i)
        i.response.send_message.assert_awaited_once()
        self.assertIn("Heurr", i.response.send_message.call_args.args[0])
        self.assertTrue(i.response.send_message.call_args.kwargs["ephemeral"])
        self.ns["display_wiki_entry"].assert_not_awaited()

    async def test_existing_entry_opens_with_map_in_history(self):
        entry = ("heurr", "Heurr", "Location", "", "", None)
        self.ns["get_map_entry"].return_value = entry
        i = interaction()
        view = self.ns["CorunumMapView"](7, history=[{"type": "home"}])
        view.location_select._values = ["13"]
        await view.select_location(i)
        self.ns["display_wiki_entry"].assert_awaited_once_with(
            i, entry, history=[{"type": "home"}, {"type": "map"}]
        )

    async def test_adlucem_pin_uses_current_entry_title(self):
        i = interaction()
        view = self.ns["CorunumMapView"](7)
        view.location_select._values = ["1"]
        await view.select_location(i)
        self.ns["get_map_entry"].assert_called_once_with("Adlucem")
        self.assertIn("#1 Adlucem", i.response.send_message.call_args.args[0])

    async def test_other_user_cannot_select_or_navigate(self):
        i = interaction(user_id=8)
        view = self.ns["CorunumMapView"](7, history=[{"type": "home"}])
        view.location_select._values = ["13"]
        await view.select_location(i)
        self.ns["get_map_entry"].assert_not_called()
        await view.go_back(i)
        self.ns["navigate_to_state"].assert_not_awaited()
        self.assertEqual(i.response.send_message.await_count, 2)

    async def test_newly_created_entry_becomes_available_by_title(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "wiki.db")
            connection = sqlite3.connect(database)
            try:
                connection.execute("""
                    CREATE TABLE wiki_entries (
                        id TEXT, title TEXT, category TEXT,
                        content TEXT, tags TEXT, image_url TEXT
                    )
                """)
                connection.execute(
                    "INSERT INTO wiki_entries VALUES (?, ?, ?, ?, ?, ?)",
                    ("heurr", "Heurr", "Location", "Town", "", None)
                )
                connection.execute(
                    "INSERT INTO wiki_entries VALUES (?, ?, ?, ?, ?, ?)",
                    ("dreamers-library", "Dreamer's Library", "Location",
                     "Library", "", None)
                )
                connection.execute(
                    "INSERT INTO wiki_entries VALUES (?, ?, ?, ?, ?, ?)",
                    ("not-location", "Flintmire", "World", "", "", None)
                )
                connection.commit()
            finally:
                connection.close()
            namespace = {"sqlite3": sqlite3, "DATABASE": database}
            helper = next(
                node for node in nodes
                if isinstance(node, ast.FunctionDef)
                and node.name == "get_map_entry"
            )
            exec(compile(ast.Module(body=[helper], type_ignores=[]),
                         str(SOURCE), "exec"), namespace)
            self.assertEqual(namespace["get_map_entry"]("heurr")[0], "heurr")
            self.assertEqual(
                namespace["get_map_entry"]("Dreamer’s Library")[0],
                "dreamers-library"
            )
            self.assertIsNone(namespace["get_map_entry"]("Not yet created"))
            self.assertIsNone(namespace["get_map_entry"]("Flintmire"))


if __name__ == "__main__":
    unittest.main()
