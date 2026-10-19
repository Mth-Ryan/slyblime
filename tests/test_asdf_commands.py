import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class AsdfCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global ASDF_MODULE, SLY_STUB, UTIL_STUB
        sublime = types.ModuleType("sublime")
        sublime.MONOSPACE_FONT = 0
        plugin_api = types.ModuleType("sublime_plugin")
        plugin_api.WindowCommand = type("WindowCommand", (), {})
        SLY_STUB = types.ModuleType("src.sly")
        SLY_STUB.loop = types.SimpleNamespace(is_running=lambda: True)
        SLY_STUB.sessions = types.SimpleNamespace(get_by_window=lambda window: None)
        UTIL_STUB = types.ModuleType("src.util")
        UTIL_STUB.show_quick_panel = None
        for name, module in (("sublime", sublime), ("sublime_plugin", plugin_api),
                             ("src.sly", SLY_STUB), ("src.util", UTIL_STUB)):
            sys.modules[name] = module
        ASDF_MODULE = importlib.import_module("src.asdf_commands")

    @classmethod
    def tearDownClass(cls):
        for name in ("src.asdf_commands", "src.sly", "src.util",
                     "sublime", "sublime_plugin"):
            sys.modules.pop(name, None)

    def test_finds_asd_files_recursively_and_skips_generated_trees(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary_directory:
            root = Path(temporary_directory)
            (root / "app.asd").write_text("", encoding="utf-8")
            nested = root / "modules"
            nested.mkdir()
            (nested / "utils.asd").write_text("", encoding="utf-8")
            ignored = root / ".git"
            ignored.mkdir()
            (ignored / "old.asd").write_text("", encoding="utf-8")

            found = ASDF_MODULE.find_asd_files([root])

            self.assertEqual([path.name for path in found], ["app.asd", "utils.asd"])

    def test_load_form_registers_project_tree_and_loads_system(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary_directory:
            root = Path(temporary_directory)
            asd_file = root / "My-System.asd"
            form = ASDF_MODULE.asdf_load_form(asd_file, root)

            self.assertIn("(:tree #p", form)
            self.assertIn(":inherit-configuration", form)
            self.assertIn('(asdf:load-system "my-system")', form)
            self.assertIn(ASDF_MODULE._lisp_string(
                root.resolve().as_posix().rstrip("/") + "/"), form)

    def test_quick_panel_choice_is_loaded_through_slynk(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary_directory:
            root = Path(temporary_directory)
            asd_file = root / "example.asd"
            asd_file.write_text("", encoding="utf-8")
            calls = []

            class Slynk:
                async def eval(self, expression, is_region, package):
                    calls.append((expression, is_region, package))
                    return "loaded"

            class Window:
                def status_message(self, message):
                    self.status = message

            session = types.SimpleNamespace(
                slynk=Slynk(), filename_translator=ASDF_MODULE.IdentityTranslator())
            SLY_STUB.sessions.get_by_window = lambda window: session

            async def choose_first(loop, window, items, flags):
                self.assertEqual(items, [str(asd_file)])
                return 0

            UTIL_STUB.show_quick_panel = choose_first
            command = ASDF_MODULE.SlyLoadAsdfProjectCommand()
            command.window = Window()
            coroutine = command.async_run([asd_file], [root])
            with self.assertRaises(StopIteration):
                coroutine.send(None)

            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0], ("(require :asdf)", False, "CL-USER"))
            self.assertFalse(calls[1][1])
            self.assertEqual(calls[1][2], "CL-USER")
            self.assertIn('(asdf:load-system "example")', calls[1][0])
            self.assertIn("Loaded ASDF system example: loaded", command.window.status)


if __name__ == "__main__":
    unittest.main()
