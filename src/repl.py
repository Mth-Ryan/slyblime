"""Sublime Text view and commands for Slynk's channel based REPL."""

import asyncio
import itertools
import sublime
import sublime_plugin

from . import sly, util


def prepare_backtrack_phantom(region, group_index, value_index):
    prefix, infix, postfix = sly.settings().get("repl")["backtracking"]["affixes"]
    return sublime.Phantom(
        region,
        f"{prefix}{group_index}{infix}{value_index}{postfix}",
        sublime.LAYOUT_INLINE,
    )


class SlyInsertTextCommand(sublime_plugin.TextCommand):
    """Insert text in a view without relying on SublimeREPL commands."""

    def run(self, edit, pos, text):
        self.view.insert(edit, int(pos), text)


class SlyReplAppendCommand(sublime_plugin.TextCommand):
    """Append server output while preserving the REPL's read-only state."""

    def run(self, edit, text, read_only=None):
        view = self.view
        previous_read_only = view.is_read_only()
        view.set_read_only(False)
        end = view.size()
        view.insert(edit, end, text)
        end += len(text)
        view.sel().clear()
        view.sel().add(sublime.Region(end, end))
        view.show(end)
        view.set_read_only(previous_read_only if read_only is None else read_only)


class SlyReplView:
    def __init__(self, session, view, repl):
        self.session = session
        self.view = view
        self.repl = repl
        self.channel_id = repl.channel.id
        self.input_start = view.size()
        self.closed_view = False
        self.value_phantom_groups = []
        self.backtrack_phantom_set = sublime.PhantomSet(view, "backtracking")
        view.settings().set("is-sly-repl", True)
        view.settings().set("sly-session-id", session.id)
        view.settings().set("sly-channel-id", self.channel_id)
        view.set_read_only(True)
        repl.bind(
            write_string=self.on_print,
            write_values=self.on_write_values,
            prompt=self.on_prompt,
            evaluation_aborted=self.on_evaluation_aborted,
            server_side_repl_close=self.on_server_side_repl_close,
        )
        repl.play_events()

    def append(self, text, read_only=None):
        if not self.closed_view:
            args = {"text": str(text)}
            if read_only is not None:
                args["read_only"] = read_only
            self.view.run_command("sly_repl_append", args)

    def final_character(self):
        return self.view.substr(self.view.size() - 1) if self.view.size() else ""

    def fresh_line(self):
        if self.view.size() and self.final_character() != "\n":
            self.append("\n")

    def on_print(self, message, *args):
        self.fresh_line()
        text = str(message)
        if (sly.settings().get("repl").get("avoid_double_newline", True)
                and self.final_character() == "\n" and text.startswith("\n")):
            text = text[1:]
        self.append(text)

    def on_write_values(self, values, *args):
        prefix = sly.settings().get("repl").get("value_prefix", "⟹ ")
        group_index = len(self.value_phantom_groups)
        phantoms = []
        for value_index, value in enumerate(values):
            self.fresh_line()
            point = self.view.size()
            phantoms.append(prepare_backtrack_phantom(
                sublime.Region(point, point), group_index, value_index))
            self.append(prefix + str(value[0]))
        self.value_phantom_groups.append(phantoms)

    def show_backtrack_phantoms(self, group_index=None, value_index=None):
        if group_index is None:
            phantoms = list(itertools.chain.from_iterable(self.value_phantom_groups))
        elif value_index is None:
            phantoms = self.value_phantom_groups[group_index]
        else:
            phantoms = [self.value_phantom_groups[group_index][value_index]]
        self.backtrack_phantom_set.update(phantoms)

    def hide_backtrack_phantoms(self):
        self.backtrack_phantom_set.update([])

    def on_prompt(self, package, prompt, error_level, *args):
        config = sly.settings().get("repl")
        self.fresh_line()
        if error_level:
            prompt = f"{prompt}{config['error'][0]}{error_level}{config['error'][1]}"
        prompt += config.get("prompt", "⟩ ")
        start = self.view.size()
        self.append(prompt, read_only=False)
        end = self.view.size()
        self.input_start = end
        self.view.settings().set("package", package)
        self.view.settings().set("prompt-region", [start, end])

    def on_evaluation_aborted(self, *data):
        self.fresh_line()
        self.append("Evaluation aborted for " + " ".join(map(str, data)))

    def on_server_side_repl_close(self, *data):
        self.close("[🏁 Slynk closed this REPL channel]")

    def close(self, message="[🏁 Slynk session disconnected]"):
        if self.closed_view:
            return
        self.append("\n" + message, read_only=True)
        self.closed_view = True
        self.view.set_name("🏁" + self.view.name())
        self.view.settings().set("sly-open-repl", False)


def get_repl_view(view):
    if not view or not view.settings().get("is-sly-repl"):
        return None
    session_id = view.settings().get("sly-session-id")
    channel_id = view.settings().get("sly-channel-id")
    session = sly.sessions.sessions.get(session_id)
    if session is None:
        return None
    repl_view = session.repl_views.get(channel_id)
    return repl_view if repl_view and repl_view.view.id() == view.id() else None


async def create_main_repl(session, window=None):
    window = window or session.window
    repl = await session.slynk.create_repl()
    view = window.new_file()
    view.set_scratch(True)
    view.assign_syntax(sly.settings().get("repl")["syntax"])
    repl_view = SlyReplView(session, view, repl)
    session.repl_views[repl.channel.id] = repl_view
    view.set_name(util.view_name(
        session.slynk,
        sly.settings().get("repl")["view_title_affixes"],
        repl.channel.id,
    ))
    view.settings().set("sly-open-repl", True)
    return repl_view


class SlyCreateReplCommand(sublime_plugin.WindowCommand):
    def run(self, **kwargs):
        session = sly.sessions.get_by_window(self.window)
        if session is not None:
            asyncio.run_coroutine_threadsafe(create_main_repl(session), sly.loop)


class SlyOpenReplCommand(sublime_plugin.WindowCommand):
    def run(self, **kwargs):
        session = sly.sessions.get_by_window(self.window)
        if session is None:
            return
        asyncio.run_coroutine_threadsafe(self.async_run(session), sly.loop)

    async def async_run(self, session):
        live_repls = [item for item in session.repl_views.values() if not item.closed_view]
        choices = [["Create new REPL", "Open a new Slynk channel"]]
        choices += [[item.view.name(), "Switch to this REPL"] for item in live_repls]
        selected = await util.show_quick_panel(sly.loop, self.window, choices, 0)
        if selected < 0:
            return
        if selected == 0:
            repl_view = await create_main_repl(session, self.window)
        else:
            repl_view = live_repls[selected - 1]
        self.window.focus_view(repl_view.view)


class SlyReplSubmitCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        repl_view = get_repl_view(self.view)
        if repl_view is None or repl_view.closed_view:
            return
        selection = self.view.sel()[0]
        end = self.view.size()
        if selection.end() != end or selection.begin() < repl_view.input_start:
            self.view.window().status_message("Enter is available at the current REPL prompt")
            return
        expression = self.view.substr(sublime.Region(repl_view.input_start, end))
        repl_view.append("\n", read_only=True)
        self.view.settings().set("sly-open-repl", True)
        repl_view.session.loop.call_soon_threadsafe(repl_view.repl.process, expression)


class SlyReplNewlineCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        repl_view = get_repl_view(self.view)
        if repl_view is None or self.view.is_read_only():
            return
        point = self.view.sel()[0].begin()
        if point < repl_view.input_start:
            self.view.window().status_message("Input can only be added at the current REPL prompt")
            return
        self.view.insert(edit, point, "\n")


class SlyReplListener(sublime_plugin.EventListener):
    def on_modified(self, view):
        repl_view = get_repl_view(view)
        if repl_view is None:
            return
        if view.is_read_only():
            view.erase_regions("backtracking")
            repl_view.hide_backtrack_phantoms()
            return

        cursor = view.sel()[0].begin()
        match = util.find_closest(view, cursor, r"(?i)#v([0-9|\w]*(:[0-9]*)?)?\w*")
        if not match or not match.contains(cursor):
            repl_view.hide_backtrack_phantoms()
            view.erase_regions("backtracking")
            return

        text = view.substr(match)
        parts = [util.safe_int(index) for index in text[2:].split(":")]
        failed = False
        try:
            if len(parts) < 2 or parts[0] is not None:
                repl_view.show_backtrack_phantoms(*parts)
            else:
                failed = True
        except (IndexError, ValueError):
            failed = True
            if len(parts) == 2 and parts[0] is not None:
                try:
                    repl_view.show_backtrack_phantoms(parts[0])
                except IndexError:
                    pass

        config = sly.settings().get("repl")["backtracking"]
        style = config["invalid_region" if failed or text.endswith(":") or not parts
                       else "valid_region"]
        view.add_regions(
            "backtracking", [match], style["scope"], "",
            util.compute_flags(style["flags"]),
        )

    def on_selection_modified(self, view):
        self.on_modified(view)

    def on_close(self, view):
        repl_view = get_repl_view(view)
        if repl_view:
            repl_view.closed_view = True
            repl_view.repl.pause_events()
            repl_view.session.repl_views.pop(repl_view.channel_id, None)
