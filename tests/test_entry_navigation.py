"""Phase 4B entry-page display tests without importing or starting the bot."""

import ast
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord


SOURCE = Path(__file__).resolve().parents[1] / "bot.py"
NAMES = {
    "make_entry_state",
    "WikiEntryView",
    "navigate_to_state",
    "build_wiki_entry_embed",
    "display_wiki_entry",
    "wiki",
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


def interaction(user_id=7):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            defer=AsyncMock(),
        ),
    )


class EntryNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entry = (
            "entry", "Entry", "World", "Legacy mirror", "tag",
            "https://example.com/image.png",
        )
        self.pages = {1: "Stored one", 2: "Stored two", 3: "Stored three"}
        self.ns = {
            "discord": discord,
            "get_entry_page_count": Mock(return_value=1),
            "get_entry_page": Mock(side_effect=self.get_page),
            "get_relationships": Mock(return_value=[]),
            "get_entry": Mock(return_value=self.entry),
            "display_codex_home": AsyncMock(),
            "display_category": AsyncMock(),
            "display_search_results": AsyncMock(),
            "display_tag_browser": AsyncMock(),
            "display_tag_results": AsyncMock(),
        }
        exec(compile(MODULE, str(SOURCE), "exec"), self.ns)

    def get_page(self, entry_id, page_number):
        content = self.pages.get(page_number)
        if content is None:
            return None
        return page_number, page_number, content

    def labels(self, view):
        return [child.label for child in view.children]

    async def test_single_page_uses_page_store_without_visual_page_controls(self):
        embed, page_number, total_pages = self.ns["build_wiki_entry_embed"](
            self.entry
        )
        self.assertEqual(embed.description, "Stored one")
        self.assertEqual(embed.footer.text, "Wiki ID: entry")
        self.assertEqual(embed.image.url, "https://example.com/image.png")
        self.assertEqual((page_number, total_pages), (1, 1))

        view = self.ns["WikiEntryView"](
            "entry", 7, history=[{"type": "home"}]
        )
        self.assertEqual(self.labels(view), ["Back", "Home"])

    async def test_multi_page_embed_and_controls_reflect_current_page(self):
        self.ns["get_entry_page_count"].return_value = 3
        embed, page_number, total_pages = self.ns["build_wiki_entry_embed"](
            self.entry, 2
        )
        self.assertEqual(embed.description, "Stored two")
        self.assertEqual(embed.footer.text, "Wiki ID: entry • Page 2 / 3")
        self.assertEqual((page_number, total_pages), (2, 3))

        middle = self.ns["WikiEntryView"](
            "entry", 7, history=[{"type": "home"}],
            page_number=2, total_pages=3,
        )
        self.assertEqual(
            self.labels(middle),
            ["Previous", "Page 2 / 3", "Next", "Back", "Home"],
        )
        self.assertFalse(middle.children[0].disabled)
        self.assertTrue(middle.children[1].disabled)
        self.assertFalse(middle.children[2].disabled)

        first = self.ns["WikiEntryView"](
            "entry", 7, page_number=1, total_pages=3
        )
        last = self.ns["WikiEntryView"](
            "entry", 7, page_number=3, total_pages=3
        )
        self.assertTrue(first.children[0].disabled)
        self.assertTrue(last.children[2].disabled)

    async def test_multi_page_view_fits_maximum_relationship_buttons(self):
        self.ns["get_relationships"].return_value = [
            ("related_to", f"entry-{index}", f"Entry {index}", "World")
            for index in range(20)
        ]
        view = self.ns["WikiEntryView"](
            "entry", 7, history=[{"type": "home"}],
            page_number=2, total_pages=3,
        )
        self.assertEqual(len(view.children), 25)
        self.assertEqual(
            self.labels(view)[:5],
            ["Previous", "Page 2 / 3", "Next", "Back", "Home"],
        )

    async def test_page_controls_are_owner_bound_and_keep_history(self):
        self.ns["get_entry_page_count"].return_value = 3
        self.ns["display_wiki_entry"] = AsyncMock()
        history = [{"type": "category", "category": "World", "page": 0}]
        view = self.ns["WikiEntryView"](
            "entry", 7, history=history, page_number=2, total_pages=3
        )

        outsider = interaction(8)
        await view.next_page(outsider)
        self.assertTrue(
            outsider.response.send_message.call_args.kwargs["ephemeral"]
        )
        self.ns["display_wiki_entry"].assert_not_awaited()

        i = interaction()
        await view.next_page(i)
        self.ns["display_wiki_entry"].assert_awaited_once_with(
            i, self.entry, history=history, page_number=3
        )

    async def test_related_entry_back_state_preserves_current_page(self):
        self.ns["display_wiki_entry"] = AsyncMock()
        self.ns["get_relationships"].return_value = [
            ("related_to", "other", "Other", "NPC")
        ]
        other = ("other", "Other", "NPC", "Other", "", None)
        self.ns["get_entry"].return_value = other
        history = [{"type": "home"}]
        view = self.ns["WikiEntryView"](
            "entry", 7, history=history, page_number=2, total_pages=3
        )
        related_button = next(
            child for child in view.children
            if child.label.startswith("Related To:")
        )

        i = interaction()
        await related_button.callback(i)
        self.ns["display_wiki_entry"].assert_awaited_once_with(
            i,
            other,
            history=history + [{
                "type": "entry", "entry_id": "entry", "page_number": 2
            }],
        )

    async def test_navigation_dispatch_restores_entry_page(self):
        self.ns["display_wiki_entry"] = AsyncMock()
        state = self.ns["make_entry_state"]("entry", 3)
        self.assertEqual(state, {
            "type": "entry", "entry_id": "entry", "page_number": 3
        })
        i = interaction()
        await self.ns["navigate_to_state"](i, state, [{"type": "home"}])
        self.ns["display_wiki_entry"].assert_awaited_once_with(
            i, self.entry, history=[{"type": "home"}], page_number=3
        )

    async def test_missing_empty_and_stale_page_states_are_safe(self):
        self.ns["get_entry_page_count"].return_value = 0
        self.assertIsNone(self.ns["build_wiki_entry_embed"](self.entry))

        i = interaction()
        await self.ns["display_wiki_entry"](i, self.entry)
        self.assertIn(
            "no readable pages",
            i.response.send_message.call_args.args[0],
        )
        i.response.edit_message.assert_not_awaited()

        self.ns["get_entry_page_count"].return_value = 3
        display = self.ns["build_wiki_entry_embed"](self.entry, 99)
        self.assertEqual(display[0].description, "Stored three")
        self.assertEqual(display[1:], (3, 3))

        self.ns["get_entry_page"].side_effect = None
        self.ns["get_entry_page"].return_value = None
        self.assertIsNone(self.ns["build_wiki_entry_embed"](self.entry, 2))

    async def test_slash_command_uses_shared_page_renderer(self):
        self.ns["get_entry_page_count"].return_value = 3
        i = interaction()
        await self.ns["wiki"](i, "entry")
        call = i.response.send_message.call_args
        self.assertEqual(call.kwargs["embed"].description, "Stored one")
        view = call.kwargs["view"]
        self.assertIsInstance(view, self.ns["WikiEntryView"])
        self.assertEqual((view.page_number, view.total_pages), (1, 3))


if __name__ == "__main__":
    unittest.main()
