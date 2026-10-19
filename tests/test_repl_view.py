import importlib
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class Region:
    def __init__(self, begin, end=None):
        self._begin = begin
        self._end = begin if end is None else end

    def begin(self):
        return self._begin

    def end(self):
        return self._end


class FakeSelection(list):
    def clear(self):
        del self[:]

    def add(self, region):
        self.append(region)


class FakeSettings:
    def __init__(self):
        self.values = {}

    def set(self, key, value):
        self.values[key] = value

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeView:
    def __init__(self):
        self.text = ""
        self._id = 22
        self._settings = FakeSettings()
        self.read_only = False
        self.selection = FakeSelection([Region(0)])
        self.name_value = "REPL"
        self.status = {}

    def id(self):
        return self._id

    def settings(self):
        return self._settings

    def size(self):
        return len(self.text)

    def substr(self, region):
        if isinstance(region, int):
            return self.text[region:region + 1]
        return self.text[region.begin():region.end()]

    def insert(self, edit, point, text):
        self.text = self.text[:point] + text + self.text[point:]

    def replace(self, edit, region, text):
        self.text = self.text[:region.begin()] + text + self.text[region.end():]

    def run_command(self, name, args):
        if name == "sly_repl_append":
            command = REPL_MODULE.SlyReplAppendCommand(self)
            command.run(object(), **args)
        elif name == "sly_repl_restore_snapshot":
            command = REPL_MODULE.SlyReplRestoreSnapshotCommand(self)
            command.run(object(), **args)

    def sel(self):
        return self.selection

    def is_read_only(self):
        return self.read_only

    def set_read_only(self, value):
        self.read_only = value

    def show(self, point):
        pass

    def erase_regions(self, name):
        pass

    def set_name(self, name):
        self.name_value = name

    def name(self):
        return self.name_value

    def set_status(self, key, value):
        self.status[key] = value

    def window(self):
        return FakeWindow()


class FakeWindow:
    def status_message(self, message):
        self.message = message


class FakeRepl:
    def __init__(self):
        self.channel = types.SimpleNamespace(id=3)
        self.callbacks = {}
        self.sent = []

    def bind(self, **callbacks):
        self.callbacks.update(callbacks)

    def play_events(self):
        pass

    def pause_events(self):
        pass

    def process(self, expression):
        self.sent.append(expression)


class FakeLoop:
    def __init__(self):
        self.scheduled = []

    def call_soon_threadsafe(self, callback, *args):
        self.scheduled.append((callback, args))
        callback(*args)


class ReplViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global REPL_MODULE
        sublime = types.ModuleType("sublime")
        sublime.Region = Region
        sublime.LAYOUT_INLINE = 0
        sublime.Phantom = lambda *args: args
        sublime.PhantomSet = lambda *args: types.SimpleNamespace(update=lambda value: None)
        plugin_api = types.ModuleType("sublime_plugin")

        class TextCommand:
            def __init__(self, view=None):
                self.view = view

        class WindowCommand:
            def __init__(self, window=None):
                self.window = window

        plugin_api.TextCommand = TextCommand
        plugin_api.WindowCommand = WindowCommand
        plugin_api.EventListener = type("EventListener", (), {})
        sys.modules["sublime"] = sublime
        sys.modules["sublime_plugin"] = plugin_api

        sly = types.ModuleType("src.sly")
        sly.settings = lambda: {"repl": {
            "prompt": "⟩ ", "error": [" ｢", "｣"],
            "value_prefix": "⟹ ", "avoid_double_newline": True,
            "backtracking": {"affixes": ["[", ":", "]"]},
        }}
        sly.sessions = types.SimpleNamespace(sessions={})
        sly.loop = FakeLoop()
        util = types.ModuleType("src.util")
        util.view_name = lambda *args: "Sly REPL"
        util.find_closest = lambda *args: None
        sys.modules["src.sly"] = sly
        sys.modules["src.util"] = util
        REPL_MODULE = importlib.import_module("src.repl")

    @classmethod
    def tearDownClass(cls):
        for name in ("src.repl", "src.sly", "src.util", "sublime", "sublime_plugin"):
            sys.modules.pop(name, None)

    def test_prompt_enables_input_and_updates_current_package(self):
        view = FakeView()
        session = types.SimpleNamespace(id=7, repl_views={})
        repl = FakeRepl()
        repl_view = REPL_MODULE.SlyReplView(session, view, repl)

        repl_view.on_prompt("COMMON-LISP-USER", ">", 0)

        self.assertEqual(view.text, ">⟩ ")
        self.assertFalse(view.is_read_only())
        self.assertEqual(view.settings().get("package"), "COMMON-LISP-USER")
        self.assertEqual(repl_view.input_start, len(view.text))

    def test_write_values_renders_values_from_current_slynk_mrepl(self):
        view = FakeView()
        session = types.SimpleNamespace(id=7, repl_views={})
        repl_view = REPL_MODULE.SlyReplView(session, view, FakeRepl())

        repl_view.on_write_values([[
            "42 (6 bits, #x2A, #o52, #b101010)", 0, "42",
        ]])

        self.assertEqual(
            view.text,
            "⟹ 42 (6 bits, #x2A, #o52, #b101010)",
        )
        self.assertTrue(view.is_read_only())
        self.assertEqual(len(repl_view.value_phantom_groups), 1)

    def test_submit_sends_only_text_after_current_prompt_and_locks_view(self):
        view = FakeView()
        loop = FakeLoop()
        session = types.SimpleNamespace(id=7, repl_views={}, loop=loop)
        repl = FakeRepl()
        repl_view = REPL_MODULE.SlyReplView(session, view, repl)
        session.repl_views[repl.channel.id] = repl_view
        REPL_MODULE.sly.sessions.sessions[session.id] = session
        repl_view.on_prompt("CL-USER", "CL-USER", 0)
        view.text += "(+ 1 2)"
        view.selection = FakeSelection([Region(len(view.text))])

        REPL_MODULE.SlyReplSubmitCommand(view).run(object())

        self.assertEqual(repl.sent, ["(+ 1 2)"])
        self.assertTrue(view.is_read_only())
        self.assertTrue(view.text.endswith("\n"))
        REPL_MODULE.sly.sessions.sessions.clear()

    def test_edits_to_prompt_and_repl_history_are_restored(self):
        view = FakeView()
        session = types.SimpleNamespace(id=7, repl_views={}, loop=FakeLoop())
        repl = FakeRepl()
        repl_view = REPL_MODULE.SlyReplView(session, view, repl)
        session.repl_views[repl.channel.id] = repl_view
        REPL_MODULE.sly.sessions.sessions[session.id] = session
        repl_view.on_prompt("CL-USER", "CL-USER", 0)

        view.text += "(+ 20 22)"
        view.selection = FakeSelection([Region(view.size())])
        REPL_MODULE.SlyReplListener().on_modified(view)
        REPL_MODULE.SlyReplSubmitCommand(view).run(object())
        repl_view.on_write_values([["42", 0, "42"]])
        repl_view.on_prompt("CL-USER", "CL-USER", 0)
        expected = view.text
        expected_input_start = repl_view.input_start

        # Simulate deleting text from the beginning of the transcript, including
        # the first prompt; Sublime's listener must restore the immutable prefix.
        view.text = view.text[1:]
        view.selection = FakeSelection([Region(0)])
        REPL_MODULE.SlyReplListener().on_modified(view)

        self.assertEqual(view.text, expected)
        self.assertGreaterEqual(view.sel()[0].begin(), expected_input_start)
        REPL_MODULE.sly.sessions.sessions.clear()


if __name__ == "__main__":
    unittest.main()
