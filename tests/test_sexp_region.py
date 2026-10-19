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


class FakeLispView:
    def __init__(self, text):
        self.text = text

    def size(self):
        return len(self.text)

    def sel(self):
        return [Region(0)]

    def scope_name(self, point):
        depth = 0
        for index, char in enumerate(self.text):
            if index == point:
                if char == "(":
                    return "source.lisp meta.parens.lisp punctuation.section.parens.begin.lisp"
                if char == ")":
                    return "source.lisp " + " ".join(
                        ["meta.parens.lisp"] * depth
                        + ["punctuation.section.parens.end.lisp"]
                    )
                return "source.lisp " + " ".join(["meta.parens.lisp"] * depth)
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
        return "source.lisp"


class SExpressionRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sublime = types.ModuleType("sublime")
        sublime.Region = Region
        sys.modules["sublime"] = sublime
        cls.util = importlib.import_module("src.util")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("src.util", None)
        sys.modules.pop("sublime", None)

    def assert_form_at(self, text, point, expected):
        view = FakeLispView(text)
        region = self.util.find_containing_form(view, point)
        self.assertIsNotNone(region)
        self.assertEqual(text[region.begin():region.end()], expected)

    def test_form_at_first_character_and_on_open_delimiter(self):
        self.assert_form_at("(+ 1 2)", 0, "(+ 1 2)")

    def test_form_before_and_after_closing_delimiter(self):
        text = "(+ 1 2)"
        self.assert_form_at(text, text.index(")"), text)
        self.assert_form_at(text, len(text), text)

    def test_nested_form_is_selected_from_its_closing_delimiter(self):
        text = "(+ 1 (* 2 3))"
        self.assert_form_at(text, text.index("(*"), "(* 2 3)")
        self.assert_form_at(text, text.index(")"), "(* 2 3)")


if __name__ == "__main__":
    unittest.main()
