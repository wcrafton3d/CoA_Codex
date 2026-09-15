"""Phase 4C page-management UI tests without importing or starting the bot."""

import ast
from pathlib import Path
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord


SOURCE = Path(__file__).resolve().parents[1] / "bot.py"
NAMES = {
    "send_entry_page_manager",
    "WikiEntryPageAddModal",
    "WikiEntryPageEditModal",
    "WikiEntryPageDeleteConfirmationView",
    "WikiEntryPageManagementView",
    "WikiEntrySearchModal",
    "WikiSearchResultView",
    "WikiManagementView",
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
            edit_message=AsyncMock(),
            send_modal=AsyncMock(),
        ),
    )


class PageManagementUiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entry = ("entry", "Entry", "World", "One", "tag", None)
        self.pages = [(11, 1, "One"), (12, 2, "Two"), (13, 3, "Three")]
        self.ns = {
            "discord": discord,
            "sqlite3": sqlite3,
            "DATABASE": "unused.db",
            "get_entry": Mock(return_value=self.entry),
            "get_entry_pages": Mock(return_value=self.pages),
            "add_entry_page": Mock(return_value="success"),
            "update_entry_page": Mock(return_value="success"),
            "delete_entry_page": Mock(return_value="success"),
            "move_entry_page": Mock(return_value="success"),
            "require_worldbuilder": AsyncMock(return_value=True),
            "send_entry_category_picker": AsyncMock(),
            "get_categories": Mock(return_value=["World"]),
            "display_codex_home": AsyncMock(),
        }
        exec(compile(MODULE, str(SOURCE), "exec"), self.ns)

    def labels(self, view):
        return [child.label for child in view.children]

    def set_content(self, modal, value):
        modal.content._value = value

    async def test_manager_layout_single_and_multiple_pages(self):
        single = self.ns["WikiEntryPageManagementView"](
            7, self.entry, self.pages[:1]
        )
        controls = {
            child.label: child for child in single.children
            if hasattr(child, "label")
        }
        self.assertIn("Add Page", controls)
        self.assertFalse(controls["Add Page"].disabled)
        self.assertFalse(controls["Edit Page"].disabled)
        self.assertTrue(controls["Delete Page"].disabled)
        self.assertTrue(controls["Move Up"].disabled)
        self.assertTrue(controls["Move Down"].disabled)

        middle = self.ns["WikiEntryPageManagementView"](
            7, self.entry, self.pages, selected_page_id=12
        )
        controls = {
            child.label: child for child in middle.children
            if hasattr(child, "label")
        }
        self.assertIn("Selected: **Page 2**", middle.prompt())
        self.assertFalse(controls["Delete Page"].disabled)
        self.assertFalse(controls["Move Up"].disabled)
        self.assertFalse(controls["Move Down"].disabled)

    async def test_manager_paginates_page_choices_by_stable_id(self):
        pages = [(index, index, f"Content {index}") for index in range(1, 27)]
        view = self.ns["WikiEntryPageManagementView"](
            7, self.entry, pages, selected_page_id=26
        )
        select = view.children[0]
        self.assertEqual(view.page, 1)
        self.assertEqual([option.value for option in select.options], ["26"])
        self.assertIn("Page list 2 of 2", view.prompt())

        self.ns["send_entry_page_manager"] = AsyncMock()
        i = interaction()
        await view.previous_page(i)
        self.ns["send_entry_page_manager"].assert_awaited_once_with(
            i, "entry", page=0, edit=True
        )

    async def test_manager_is_owner_bound_and_rechecks_role(self):
        view = self.ns["WikiEntryPageManagementView"](
            7, self.entry, self.pages
        )
        outsider = interaction(8)
        self.assertFalse(await view.interaction_check(outsider))
        self.ns["require_worldbuilder"].assert_not_awaited()

        self.ns["require_worldbuilder"].return_value = False
        self.assertFalse(await view.interaction_check(interaction()))

    async def test_manager_loader_handles_current_missing_and_database_states(self):
        i = interaction()
        await self.ns["send_entry_page_manager"](i, "entry")
        call = i.response.send_message.call_args
        self.assertTrue(call.kwargs["ephemeral"])
        self.assertIsInstance(
            call.kwargs["view"], self.ns["WikiEntryPageManagementView"]
        )

        self.ns["get_entry"].return_value = None
        i = interaction()
        await self.ns["send_entry_page_manager"](i, "missing")
        self.assertIn("no longer exists", i.response.send_message.call_args.args[0])

        self.ns["get_entry"].side_effect = sqlite3.Error("forced")
        i = interaction()
        await self.ns["send_entry_page_manager"](i, "entry")
        self.assertIn("database error", i.response.send_message.call_args.args[0])

    async def test_add_modal_rechecks_role_and_refreshes_new_page(self):
        modal = self.ns["WikiEntryPageAddModal"]("entry")
        self.set_content(modal, " New page ")
        self.ns["send_entry_page_manager"] = AsyncMock()
        i = interaction()
        await modal.on_submit(i)
        self.ns["add_entry_page"].assert_called_once_with("entry", "New page")
        self.ns["send_entry_page_manager"].assert_awaited_once_with(
            i,
            "entry",
            selected_page_id=13,
            edit=True,
            notice="✅ Page added.",
        )

        self.ns["require_worldbuilder"].return_value = False
        blocked = self.ns["WikiEntryPageAddModal"]("entry")
        self.set_content(blocked, "Blocked")
        await blocked.on_submit(interaction())
        self.assertEqual(self.ns["add_entry_page"].call_count, 1)

    async def test_add_and_edit_modals_handle_validation_stale_and_db_errors(self):
        self.ns["add_entry_page"].side_effect = ValueError("Page content cannot be empty.")
        modal = self.ns["WikiEntryPageAddModal"]("entry")
        self.set_content(modal, " ")
        i = interaction()
        await modal.on_submit(i)
        self.assertIn("cannot be empty", i.response.send_message.call_args.args[0])

        self.ns["require_worldbuilder"].return_value = True
        self.ns["update_entry_page"].side_effect = None
        self.ns["update_entry_page"].return_value = "not_found"
        self.ns["send_entry_page_manager"] = AsyncMock()
        edit = self.ns["WikiEntryPageEditModal"]("entry", 12, 2, "Two")
        self.set_content(edit, "Changed")
        i = interaction()
        await edit.on_submit(i)
        self.ns["update_entry_page"].assert_called_once_with(
            "entry", 12, "Changed"
        )
        self.assertIn(
            "no longer exists",
            self.ns["send_entry_page_manager"].call_args.kwargs["notice"],
        )

        self.ns["update_entry_page"].side_effect = sqlite3.Error("forced")
        i = interaction()
        await edit.on_submit(i)
        self.assertIn("database error", i.response.send_message.call_args.args[0])

    async def test_edit_delete_and_move_recheck_stable_selection(self):
        view = self.ns["WikiEntryPageManagementView"](
            7, self.entry, self.pages, selected_page_id=12
        )
        i = interaction()
        await view.edit_page(i)
        modal = i.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, self.ns["WikiEntryPageEditModal"])
        self.assertEqual((modal.page_id, modal.page_number), (12, 2))

        i = interaction()
        await view.delete_page(i)
        confirmation = i.response.edit_message.call_args.kwargs["view"]
        self.assertIsInstance(
            confirmation, self.ns["WikiEntryPageDeleteConfirmationView"]
        )
        self.assertEqual(confirmation.page_id, 12)

        self.ns["send_entry_page_manager"] = AsyncMock()
        i = interaction()
        await view.move_up(i)
        self.ns["move_entry_page"].assert_called_once_with("entry", 12, -1)
        self.ns["send_entry_page_manager"].assert_awaited_once_with(
            i,
            "entry",
            selected_page_id=12,
            edit=True,
            notice="✅ Page moved.",
        )

        self.ns["get_entry_pages"].return_value = self.pages[:1]
        stale = self.ns["WikiEntryPageManagementView"](
            7, self.entry, self.pages, selected_page_id=12
        )
        i = interaction()
        await stale.edit_page(i)
        self.assertIn("no longer exists", i.response.send_message.call_args.args[0])

        self.ns["get_entry_pages"].return_value = [(12, 2, "x" * 4001)]
        legacy = self.ns["WikiEntryPageManagementView"](
            7, self.entry, [(12, 2, "x" * 4001)], selected_page_id=12
        )
        i = interaction()
        await legacy.edit_page(i)
        self.assertIn("4,000-character", i.response.send_message.call_args.args[0])
        i.response.send_modal.assert_not_awaited()

    async def test_delete_confirmation_rechecks_only_page_and_stale_state(self):
        self.ns["send_entry_page_manager"] = AsyncMock()
        view = self.ns["WikiEntryPageDeleteConfirmationView"](
            7, "entry", 12, 2
        )
        for result, expected in (
            ("only_page", "only remaining"),
            ("not_found", "no longer exists"),
            ("success", "Page deleted"),
        ):
            self.ns["delete_entry_page"].return_value = result
            i = interaction()
            await view.confirm_delete(i)
            notice = self.ns["send_entry_page_manager"].call_args.kwargs["notice"]
            self.assertIn(expected, notice)

        i = interaction()
        await view.cancel_delete(i)
        self.assertIn(
            "cancelled",
            self.ns["send_entry_page_manager"].call_args.kwargs["notice"],
        )

    async def test_confirmation_role_and_mutation_database_failures(self):
        confirmation = self.ns["WikiEntryPageDeleteConfirmationView"](
            7, "entry", 12, 2
        )
        outsider = interaction(8)
        self.assertFalse(await confirmation.interaction_check(outsider))

        self.ns["require_worldbuilder"].return_value = False
        self.assertFalse(await confirmation.interaction_check(interaction()))
        self.ns["require_worldbuilder"].return_value = True

        self.ns["delete_entry_page"].side_effect = sqlite3.Error("forced")
        i = interaction()
        await confirmation.confirm_delete(i)
        self.assertIn("database error", i.response.send_message.call_args.args[0])

        self.ns["move_entry_page"].side_effect = sqlite3.Error("forced")
        manager = self.ns["WikiEntryPageManagementView"](
            7, self.entry, self.pages, selected_page_id=12
        )
        i = interaction()
        await manager.move_down(i)
        self.assertIn("database error", i.response.send_message.call_args.args[0])

    async def test_management_panel_and_search_open_page_manager(self):
        panel = self.ns["WikiManagementView"](7)
        controls = {child.label: child for child in panel.children}
        self.assertIn("Manage Pages", controls)
        self.assertEqual(len(panel.children), 9)

        i = interaction()
        await controls["Manage Pages"].callback(i)
        modal = i.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, self.ns["WikiEntrySearchModal"])
        self.assertEqual(modal.mode, "pages")

        results = self.ns["WikiSearchResultView"](
            author_id=7,
            mode="pages",
            entries=[("entry", "Entry", "World")],
        )
        self.ns["send_entry_page_manager"] = AsyncMock()
        i = interaction(values=["entry"])
        await results.select_entry(i)
        self.ns["require_worldbuilder"].assert_awaited_with(i)
        self.ns["send_entry_page_manager"].assert_awaited_once_with(i, "entry")


if __name__ == "__main__":
    unittest.main()
