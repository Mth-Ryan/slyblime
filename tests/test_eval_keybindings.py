import json
import re
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_MODULE = None


def read_sublime_json(path):
    """Sublime's JSON format permits trailing commas."""
    text = path.read_text(encoding="utf-8")
    return json.loads(re.sub(r",\s*([}\]])", r"\1", text))


class EvaluationKeybindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global OUTPUT_MODULE
        sublime = types.ModuleType("sublime")
        sublime.settings = lambda: types.SimpleNamespace(get=lambda *args: {
            "form_scope": "form",
            "package_scope": "package",
        })

        class Region:
            def __init__(self, begin, end=None):
                self._begin = begin
                self._end = begin if end is None else end

            def begin(self):
                return self._begin

            def end(self):
                return self._end

        sublime.Region = Region
        plugin_api = types.ModuleType("sublime_plugin")
        plugin_api.TextCommand = type("TextCommand", (), {})
        plugin_api.WindowCommand = type("WindowCommand", (), {})
        sly = types.ModuleType("src.sly")
        sly.loop = object()
        util = types.ModuleType("src.util")
        util.current_package = lambda *args: (None, None)
        util.highlight_region = lambda *args: None
        ui = types.ModuleType("src.ui_view")
        for name, module in (("sublime", sublime), ("sublime_plugin", plugin_api),
                             ("src.sly", sly), ("src.util", util), ("src.ui_view", ui)):
            sys.modules[name] = module
        OUTPUT_MODULE = __import__("src.output_commands", fromlist=["determine_input"])

    @classmethod
    def tearDownClass(cls):
        for name in ("src.output_commands", "src.sly", "src.util", "src.ui_view",
                     "sublime", "sublime_plugin"):
            sys.modules.pop(name, None)

    def test_buffer_input_includes_the_last_character(self):
        class View:
            text = "(in-package :cl-user)\n(+ 40 2)"

            def size(self):
                return len(self.text)

            def sel(self):
                return [types.SimpleNamespace(begin=lambda: len(self.text))]

            def substr(self, region):
                return self.text[region.begin():region.end()]

        coroutine = OUTPUT_MODULE.determine_input(View(), "buffer", None)
        with self.assertRaises(StopIteration) as result:
            coroutine.send(None)
        query, package = result.exception.value
        self.assertEqual(query, View.text)
        self.assertIsNone(package)

    def test_form_input_uses_an_explicit_selection(self):
        selected_region = types.SimpleNamespace(begin=lambda: 0, end=lambda: 7)

        class Window:
            def status_message(self, message):
                self.message = message

        class View:
            text = "(+ 1 2) trailing"
            selection = [selected_region]

            def size(self):
                return len(self.text)

            def sel(self):
                return self.selection

            def substr(self, region):
                return self.text[region.begin():region.end()]

            def window(self):
                return Window()

        coroutine = OUTPUT_MODULE.determine_input(View(), "form", None)
        with self.assertRaises(StopIteration) as result:
            coroutine.send(None)
        query, package = result.exception.value
        self.assertEqual(query, "(+ 1 2)")
        self.assertIsNone(package)

    def test_lisp_evaluation_bindings_are_scoped_to_source_files(self):
        bindings = read_sublime_json(ROOT / "src" / "Default.sublime-keymap")
        by_keys = {tuple(binding["keys"]): binding for binding in bindings
                   if binding.get("command") == "sly_eval_region"}

        form = by_keys[("ctrl+enter",)]
        self.assertEqual(form["args"], {"input_source": "form"})
        mac_form = by_keys[("super+enter",)]
        self.assertEqual(mac_form["args"], {"input_source": "form"})
        buffer = by_keys[("primary+shift+enter",)]
        self.assertEqual(buffer["args"], {"input_source": "buffer"})
        for binding in (form, buffer):
            contexts = binding["context"]
            self.assertIn(
                {"key": "selector", "operator": "equal", "operand": "source.lisp"},
                contexts,
            )
            self.assertIn(
                {"key": "setting.is-sly-repl", "operator": "not_equal", "operand": True},
                contexts,
            )

    def test_plugin_does_not_override_sublime_terminal_shortcut(self):
        bindings = read_sublime_json(ROOT / "src" / "Default.sublime-keymap")
        self.assertFalse(any(binding.get("keys") == ["primary+\'"] for binding in bindings))

    def test_asdf_project_command_is_available_from_palette_and_keybinding(self):
        commands = read_sublime_json(ROOT / "src" / "Default.sublime-commands")
        command = next(item for item in commands
                       if item.get("caption") == "Sly: Load ASDF project")
        self.assertEqual(command["command"], "sly_load_asdf_project")

        bindings = read_sublime_json(ROOT / "src" / "Default.sublime-keymap")
        self.assertTrue(any(
            binding.get("keys") == ["ctrl+alt+enter"]
            and binding.get("command") == "sly_load_asdf_project"
            for binding in bindings
        ))

    def test_evaluate_buffer_commands_use_region_evaluation(self):
        def find_commands(items):
            for item in items:
                if item.get("caption") in (
                    "Sly: Evaluate contents of view",
                    "Evaluate contents of view",
                ):
                    yield item
                yield from find_commands(item.get("children", []))

        for relative_path in (
            "src/Default.sublime-commands",
            "src/Main.sublime-menu",
        ):
            data = read_sublime_json(ROOT / relative_path)
            command = next(find_commands(data))
            self.assertEqual(command["command"], "sly_eval_region")
            self.assertEqual(command["args"]["input_source"], "buffer")


if __name__ == "__main__":
    unittest.main()
