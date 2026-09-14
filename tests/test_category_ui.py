"""Phase 3C UI behavior tests without importing or starting the bot."""
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
    "category_autocomplete",
    "parse_category_sort_order",
    "CategoryAddModal",
    "CategoryEditModal",
    "CategoryDeleteConfirmationView",
    "CategoryManagementSelectView",
    "WikiManagementView",
    "wiki_category_add",
    "wiki_category_edit",
    "wiki_category_delete",
}
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
nodes = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if node.name in NAMES:
            node.decorator_list = [] if not isinstance(node, ast.ClassDef) else node.decorator_list
            nodes.append(node)
assert {node.name for node in nodes} == NAMES
MODULE = ast.Module(body=nodes, type_ignores=[])


def interaction(user_id=7, values=None):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        data={"values": values or []},
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            send_modal=AsyncMock(),
        ),
    )


class CategoryUiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ns = {
            "discord": discord,
            "app_commands": app_commands,
            "sqlite3": sqlite3,
            "get_categories": Mock(return_value=[
                "World", "Location", "NPC", "Faction", "Bestiary"
            ]),
            "create_category": Mock(return_value="success"),
            "update_category": Mock(return_value="success"),
            "delete_category": Mock(return_value="success"),
            "get_category": Mock(),
            "get_category_entry_count": Mock(),
            "require_worldbuilder": AsyncMock(return_value=True),
        }
        exec(compile(MODULE, str(SOURCE), "exec"), self.ns)

    def set_values(self, modal, **values):
        for name, value in values.items():
            getattr(modal, name)._value = value

    async def test_autocomplete_filters_in_registry_order_and_limits(self):
        choices = await self.ns["category_autocomplete"](interaction(), " ti ")
        self.assertEqual([(c.name, c.value) for c in choices], [
            ("Location", "Location"), ("Faction", "Faction"),
            ("Bestiary", "Bestiary"),
        ])
        self.ns["get_categories"].return_value = [f"C{i}" for i in range(30)]
        self.assertEqual(len(await self.ns["category_autocomplete"](
            interaction(), ""
        )), 25)

    def test_sort_order_parser(self):
        parse = self.ns["parse_category_sort_order"]
        self.assertEqual(parse(" -12 "), -12)
        for value in ("", "1.5", "word", str(2 ** 63)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse(value)

    async def test_add_modal_success_duplicate_validation_and_db_error(self):
        modal = self.ns["CategoryAddModal"]()
        self.set_values(modal, name_input="  Deities  ",
                        description_input="  Divine beings  ",
                        icon_input=" 🌟 ", sort_order_input="60")
        i = interaction()
        await modal.on_submit(i)
        self.ns["create_category"].assert_called_once_with(
            "  Deities  ", "Divine beings", "🌟", 60
        )
        self.assertIn("created", i.response.send_message.call_args.args[0])
        self.assertTrue(i.response.send_message.call_args.kwargs["ephemeral"])

        for result, expected in (("duplicate", "already exists"),):
            self.ns["create_category"].return_value = result
            i = interaction()
            await modal.on_submit(i)
            self.assertIn(expected, i.response.send_message.call_args.args[0])

        self.set_values(modal, sort_order_input="bad")
        i = interaction()
        await modal.on_submit(i)
        self.assertIn("whole number", i.response.send_message.call_args.args[0])

        self.set_values(modal, sort_order_input="60")
        self.ns["create_category"].side_effect = sqlite3.Error("forced")
        i = interaction()
        await modal.on_submit(i)
        self.assertIn("database error", i.response.send_message.call_args.args[0])

    async def test_edit_modal_prefills_and_handles_all_results(self):
        modal = self.ns["CategoryEditModal"](
            (4, "Faction", "Groups", "⚔️", 40)
        )
        self.assertEqual(modal.name_input.default, "Faction")
        self.assertEqual(modal.description_input.default, "Groups")
        self.assertEqual(modal.icon_input.default, "⚔️")
        self.assertEqual(modal.sort_order_input.default, "40")
        self.set_values(modal, name_input=" Orders ",
                        description_input=" ", icon_input=" ",
                        sort_order_input="45")

        for result, expected in (
            ("success", "updated"),
            ("duplicate", "already exists"),
            ("not_found", "no longer exists"),
        ):
            self.ns["update_category"].return_value = result
            i = interaction()
            await modal.on_submit(i)
            self.assertIn(expected, i.response.send_message.call_args.args[0])
        self.ns["update_category"].assert_called_with(
            "Faction", " Orders ", None, None, 45
        )

    async def test_delete_confirmation_is_bound_and_rechecks_usage(self):
        view = self.ns["CategoryDeleteConfirmationView"](7, "Bestiary")
        outsider = interaction(8)
        self.assertFalse(await view.interaction_check(outsider))
        self.assertTrue(outsider.response.send_message.call_args.kwargs["ephemeral"])
        self.assertTrue(await view.interaction_check(interaction(7)))

        self.ns["delete_category"].return_value = "in_use"
        i = interaction(7)
        await view.children[0].callback(i)
        self.assertIn("now contains entries",
                      i.response.edit_message.call_args.kwargs["content"])
        self.assertIsNone(i.response.edit_message.call_args.kwargs["view"])

    async def test_delete_confirmation_success_missing_error_and_cancel(self):
        for result, expected in (
            ("success", "deleted"),
            ("not_found", "no longer exists"),
        ):
            self.ns["delete_category"].return_value = result
            view = self.ns["CategoryDeleteConfirmationView"](7, "Bestiary")
            i = interaction()
            await view.children[0].callback(i)
            self.assertIn(expected, i.response.edit_message.call_args.kwargs["content"])

        self.ns["delete_category"].side_effect = sqlite3.Error("forced")
        view = self.ns["CategoryDeleteConfirmationView"](7, "Bestiary")
        i = interaction()
        await view.children[0].callback(i)
        self.assertIn("database error", i.response.edit_message.call_args.kwargs["content"])

        view = self.ns["CategoryDeleteConfirmationView"](7, "Bestiary")
        i = interaction()
        await view.children[1].callback(i)
        self.assertIn("cancelled", i.response.edit_message.call_args.kwargs["content"])

    async def test_commands_require_role_and_handle_category_state(self):
        self.ns["require_worldbuilder"].return_value = False
        i = interaction()
        await self.ns["wiki_category_add"](i)
        i.response.send_modal.assert_not_awaited()

        self.ns["require_worldbuilder"].return_value = True
        i = interaction()
        await self.ns["wiki_category_add"](i)
        self.assertIsInstance(i.response.send_modal.call_args.args[0],
                              self.ns["CategoryAddModal"])

        self.ns["get_category"].return_value = None
        for command in ("wiki_category_edit", "wiki_category_delete"):
            i = interaction()
            await self.ns[command](i, "Missing")
            self.assertIn("does not exist", i.response.send_message.call_args.args[0])

        details = (5, "Bestiary", "Creatures", "🐉", 50)
        self.ns["get_category"].return_value = details
        i = interaction()
        await self.ns["wiki_category_edit"](i, "bestiary")
        self.assertIsInstance(i.response.send_modal.call_args.args[0],
                              self.ns["CategoryEditModal"])

        self.ns["get_category_entry_count"].return_value = 2
        i = interaction()
        await self.ns["wiki_category_delete"](i, "Bestiary")
        self.assertIn("2 entries", i.response.send_message.call_args.args[0])
        self.assertNotIn("view", i.response.send_message.call_args.kwargs)

        self.ns["get_category_entry_count"].return_value = 0
        i = interaction()
        await self.ns["wiki_category_delete"](i, "Bestiary")
        call = i.response.send_message.call_args
        self.assertIsInstance(call.kwargs["view"],
                              self.ns["CategoryDeleteConfirmationView"])
        self.assertTrue(call.kwargs["ephemeral"])

    async def test_management_panel_exposes_category_actions(self):
        view = self.ns["WikiManagementView"](7)
        children = {child.label: child for child in view.children}
        self.assertIn("Create Category", children)
        self.assertIn("Edit Category", children)
        self.assertIn("Delete Category", children)

        i = interaction()
        await children["Create Category"].callback(i)
        self.assertIsInstance(
            i.response.send_modal.call_args.args[0],
            self.ns["CategoryAddModal"]
        )

        i = interaction()
        await children["Edit Category"].callback(i)
        picker = i.response.send_message.call_args.kwargs["view"]
        self.assertIsInstance(
            picker,
            self.ns["CategoryManagementSelectView"]
        )
        self.assertTrue(i.response.send_message.call_args.kwargs["ephemeral"])

        details = (5, "Bestiary", "Creatures", "🐉", 50)
        self.ns["get_category"].return_value = details
        i = interaction(values=["Bestiary"])
        await picker.children[0].callback(i)
        self.assertIsInstance(
            i.response.send_modal.call_args.args[0],
            self.ns["CategoryEditModal"]
        )

    async def test_management_delete_uses_registry_guards(self):
        view = self.ns["WikiManagementView"](7)
        delete_button = next(
            child for child in view.children
            if child.label == "Delete Category"
        )
        i = interaction()
        await delete_button.callback(i)
        picker = i.response.send_message.call_args.kwargs["view"]

        self.ns["get_category"].return_value = (
            1, "World", "Lore", "🌎", 10
        )
        self.ns["get_category_entry_count"].return_value = 2
        i = interaction(values=["World"])
        await picker.children[0].callback(i)
        self.assertIn("2 entries", i.response.send_message.call_args.args[0])
        self.assertNotIn("view", i.response.send_message.call_args.kwargs)

        self.ns["get_category_entry_count"].return_value = 0
        i = interaction(values=["World"])
        await picker.children[0].callback(i)
        confirmation = i.response.send_message.call_args.kwargs["view"]
        self.assertIsInstance(
            confirmation,
            self.ns["CategoryDeleteConfirmationView"]
        )


if __name__ == "__main__":
    unittest.main()
