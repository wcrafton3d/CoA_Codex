"""Animated home-banner behavior without importing or starting the bot."""

import ast
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord


SOURCE = Path(__file__).resolve().parents[1] / "bot.py"
NAMES = {
    "CodexHomeView",
    "build_codex_home_embeds",
    "display_codex_home",
}
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
nodes = [
    node for node in tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    and node.name in NAMES
]
assert {node.name for node in nodes} == NAMES
MODULE = ast.Module(body=nodes, type_ignores=[])


def interaction(response_done=False):
    return SimpleNamespace(
        user=SimpleNamespace(id=7),
        response=SimpleNamespace(
            is_done=Mock(return_value=response_done),
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
        ),
        edit_original_response=AsyncMock(),
    )


class HomeBannerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.categories = [
            "World", "Location", "NPC", "Faction", "Bestiary"
        ]
        self.ns = {
            "discord": discord,
            "HOME_BANNER_URL": (
                "https://raw.githubusercontent.com/wcrafton3d/"
                "CoA_Codex/main/static/coa-codex-home-banner.webp"
            ),
            "get_categories": Mock(return_value=self.categories),
            "get_category_icon": Mock(return_value="📚"),
            "make_home_state": Mock(return_value=("home",)),
            "display_category": AsyncMock(),
            "display_tag_browser": AsyncMock(),
        }
        exec(compile(MODULE, str(SOURCE), "exec"), self.ns)

    def assert_home_embeds(self, embeds):
        self.assertEqual(len(embeds), 2)
        self.assertEqual(
            embeds[0].image.url,
            self.ns["HOME_BANNER_URL"],
        )
        self.assertEqual(embeds[1].title, "📖 The CoA Codex")
        self.assertEqual(
            embeds[1].footer.text,
            f"Total categories: {len(self.categories)}",
        )

    async def test_new_home_message_places_banner_before_content(self):
        i = interaction()

        await self.ns["display_codex_home"](i)

        call = i.response.send_message.call_args
        self.assert_home_embeds(call.kwargs["embeds"])
        self.assertNotIn("embed", call.kwargs)
        self.assertIsInstance(call.kwargs["view"], self.ns["CodexHomeView"])

    async def test_home_navigation_restores_both_embeds(self):
        i = interaction()
        await self.ns["display_codex_home"](i, edit=True)
        call = i.response.edit_message.call_args
        self.assert_home_embeds(call.kwargs["embeds"])

        deferred = interaction(response_done=True)
        await self.ns["display_codex_home"](deferred, edit=True)
        call = deferred.edit_original_response.call_args
        self.assert_home_embeds(call.kwargs["embeds"])


if __name__ == "__main__":
    unittest.main()
