"""Phase 3E entry-category UI tests without importing or starting the bot."""
import ast
from pathlib import Path
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from discord import app_commands


SOURCE = Path(__file__).resolve().parents[1] / "bot.py"
NAMES = {
    "WikiAddModal",
    "wiki_add",
    "WikiEditModal",
    "EntryCategorySelectView",
    "send_entry_category_picker",
    "wiki_edit",
    "WikiSearchResultView",
}
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
nodes = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if node.name in NAMES:
            if not isinstance(node, ast.ClassDef):
                node.decorator_list = []
            nodes.append(node)
assert {node.name for node in nodes} == NAMES
MODULE = ast.Module(body=nodes, type_ignores=[])


def interaction(user_id=7, values=None):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        data={"values": values or []},
        response=SimpleNamespace(
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
            edit_message=AsyncMock(),
        ),
    )


class EntryCategoryUiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entry = (
            "entry", "Entry", "World", "Lore", "tag",
            "https://example.com/image.png"
        )
        self.ns = {
            "discord": discord,
            "app_commands": app_commands,
            "sqlite3": sqlite3,
            "get_categories": Mock(return_value=["World", "NPC", "Bestiary"]),
            "get_category": Mock(return_value=(1, "World", None, "🌎", 10)),
            "get_entry": Mock(return_value=self.entry),
            "add_entry": Mock(return_value="success"),
            "update_entry": Mock(return_value="success"),
            "require_worldbuilder": AsyncMock(return_value=True),
        }
        exec(compile(MODULE, str(SOURCE), "exec"), self.ns)

    def set_values(self, modal, **values):
        for name, value in values.items():
            getattr(modal, name)._value = value

    async def test_add_modal_uses_selected_category_and_handles_stale_choice(self):
        modal = self.ns["WikiAddModal"]("World")
        self.assertEqual(len(modal.children), 4)
        self.assertFalse(hasattr(modal, "category"))
        self.set_values(
            modal,
            entry_id=" New-Entry ",
            title_input=" New Entry ",
            tags=" tag ",
            content=" Lore "
        )
        i = interaction()
        await modal.on_submit(i)
        self.ns["add_entry"].assert_called_once_with(
            "new-entry", "New Entry", "World", "Lore", "tag"
        )
        self.assertIn("created", i.response.send_message.call_args.args[0])

        for result, expected in (
            ("duplicate", "already exists"),
            ("invalid_category", "no longer exists"),
        ):
            self.ns["add_entry"].return_value = result
            i = interaction()
            await modal.on_submit(i)
            self.assertIn(expected, i.response.send_message.call_args.args[0])

    async def test_edit_modal_uses_selected_category_and_preserves_fields(self):
        modal = self.ns["WikiEditModal"](self.entry, "NPC")
        self.assertEqual(len(modal.children), 4)
        self.assertFalse(hasattr(modal, "category"))
        self.set_values(
            modal,
            title_input=" Changed ",
            tags=" new ",
            image_url=" https://example.com/new.png ",
            content=" Changed lore "
        )
        i = interaction()
        await modal.on_submit(i)
        self.ns["update_entry"].assert_called_once_with(
            "entry", "Changed", "NPC", "Changed lore", "new",
            "https://example.com/new.png"
        )
        self.assertIn("updated", i.response.send_message.call_args.args[0])

        self.ns["update_entry"].return_value = "invalid_category"
        i = interaction()
        await modal.on_submit(i)
        self.assertIn("no longer exists", i.response.send_message.call_args.args[0])

    async def test_picker_preserves_registry_order_and_prefills_current_category(self):
        i = interaction()
        await self.ns["send_entry_category_picker"](
            i, mode="edit", entry=self.entry
        )
        picker = i.response.send_message.call_args.kwargs["view"]
        options = picker.children[0].options
        self.assertEqual([option.value for option in options], [
            "World", "NPC", "Bestiary"
        ])
        self.assertTrue(options[0].default)
        self.assertTrue(i.response.send_message.call_args.kwargs["ephemeral"])

        outsider = interaction(user_id=8)
        self.assertFalse(await picker.interaction_check(outsider))
        self.assertIn(
            "another Worldbuilder",
            outsider.response.send_message.call_args.args[0]
        )
        self.assertTrue(await picker.interaction_check(interaction()))

        self.ns["get_category"].return_value = (
            2, "NPC", None, "👤", 30
        )
        i = interaction(values=["NPC"])
        await picker.children[0].callback(i)
        modal = i.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, self.ns["WikiEditModal"])
        self.assertEqual(modal.category_value, "NPC")

    async def test_picker_pages_through_every_registered_category(self):
        categories = [f"Category {index:02}" for index in range(27)]
        self.ns["get_categories"].return_value = categories
        i = interaction()
        await self.ns["send_entry_category_picker"](i, mode="add")
        picker = i.response.send_message.call_args.kwargs["view"]
        self.assertEqual(len(picker.children[0].options), 25)
        self.assertIn("Page 1 of 2", picker.prompt())

        i = interaction()
        await picker.children[2].callback(i)
        self.assertEqual(
            [option.value for option in picker.children[0].options],
            categories[25:]
        )
        self.assertIn("Page 2 of 2", i.response.edit_message.call_args.kwargs["content"])

    async def test_picker_handles_empty_registry_and_stale_selection(self):
        self.ns["get_categories"].return_value = []
        i = interaction()
        await self.ns["send_entry_category_picker"](i, mode="add")
        self.assertIn("No registered categories", i.response.send_message.call_args.args[0])

        self.ns["get_categories"].return_value = ["World"]
        i = interaction()
        await self.ns["send_entry_category_picker"](i, mode="add")
        picker = i.response.send_message.call_args.kwargs["view"]
        self.ns["get_category"].return_value = None
        i = interaction(values=["World"])
        await picker.children[0].callback(i)
        self.assertIn("no longer exists", i.response.send_message.call_args.args[0])

    async def test_slash_and_management_search_paths_open_picker(self):
        i = interaction()
        await self.ns["wiki_add"](i)
        self.assertIsInstance(
            i.response.send_message.call_args.kwargs["view"],
            self.ns["EntryCategorySelectView"]
        )

        i = interaction()
        await self.ns["wiki_edit"](i, "entry")
        self.assertIsInstance(
            i.response.send_message.call_args.kwargs["view"],
            self.ns["EntryCategorySelectView"]
        )

        results = self.ns["WikiSearchResultView"](
            author_id=7,
            mode="edit",
            entries=[("entry", "Entry", "World")]
        )
        i = interaction(values=["entry"])
        await results.children[0].callback(i)
        self.assertIsInstance(
            i.response.send_message.call_args.kwargs["view"],
            self.ns["EntryCategorySelectView"]
        )


if __name__ == "__main__":
    unittest.main()
