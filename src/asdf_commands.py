import asyncio
import os
from pathlib import Path

import sublime
import sublime_plugin

from . import sly, util
from .filename_translation import IdentityTranslator


IGNORED_DIRECTORIES = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules",
    "__pycache__", "build", "dist",
}


def find_asd_files(project_roots):
    """Return the ASDF definitions found beneath the supplied project roots."""
    results = set()
    for root in project_roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for directory, child_directories, filenames in os.walk(str(root)):
            child_directories[:] = sorted(
                child for child in child_directories
                if child not in IGNORED_DIRECTORIES and not child.startswith(".")
            )
            for filename in filenames:
                if filename.lower().endswith(".asd"):
                    results.add(Path(directory) / filename)
    return sorted(results, key=lambda path: str(path).casefold())


def _lisp_string(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _directory_path(path):
    path = os.fspath(path).replace("\\", "/")
    return path.rstrip("/") + "/"


def asdf_load_form(asd_file, project_root):
    """Build the ASDF form sent after ASDF has been required."""
    system_name = Path(asd_file).stem.lower()
    tree = _lisp_string(_directory_path(project_root))
    system = _lisp_string(system_name)
    return (
        "(progn\n"
        "  (asdf:initialize-source-registry\n"
        f"    '(:source-registry (:tree #p{tree}) :inherit-configuration))\n"
        f"  (asdf:load-system {system}))"
    )


def _project_root_for(asd_file, project_roots):
    asd_file = Path(asd_file).resolve()
    containing_roots = []
    for root in project_roots:
        root = Path(root).resolve()
        try:
            common_path = os.path.commonpath((str(asd_file), str(root)))
            if os.path.normcase(common_path) == os.path.normcase(str(root)):
                containing_roots.append(root)
        except ValueError:
            # Paths on different Windows drives have no common ancestor.
            continue
    if containing_roots:
        return max(containing_roots, key=lambda root: len(str(root)))
    return asd_file.parent


def _window_project_roots(window):
    roots = [Path(folder) for folder in window.folders() if Path(folder).is_dir()]
    if roots:
        return roots
    view = window.active_view()
    file_name = view.file_name() if view else None
    return [Path(file_name).parent] if file_name else []


class SlyLoadAsdfProjectCommand(sublime_plugin.WindowCommand):
    def run(self):
        if not sly.loop.is_running():
            self.window.status_message("Connect to a Lisp instance first")
            return
        if sly.sessions.get_by_window(self.window) is None:
            self.window.status_message("No Slynk connection is assigned to this window")
            return
        project_roots = _window_project_roots(self.window)
        if not project_roots:
            self.window.status_message("Open a project folder or Lisp file first")
            return
        asd_files = find_asd_files(project_roots)
        if not asd_files:
            self.window.status_message("No ASDF .asd files found in the project")
            return

        asyncio.run_coroutine_threadsafe(
            self.async_run(asd_files, project_roots), sly.loop)

    async def async_run(self, asd_files, project_roots):
        labels = []
        roots_by_file = []
        for asd_file in asd_files:
            project_root = _project_root_for(asd_file, project_roots)
            labels.append(str(asd_file))
            roots_by_file.append(project_root)

        choice = await util.show_quick_panel(
            sly.loop, self.window, labels, sublime.MONOSPACE_FONT)
        if choice < 0:
            return

        asd_file = asd_files[choice]
        project_root = roots_by_file[choice]
        system_name = asd_file.stem.lower()
        session = sly.sessions.get_by_window(self.window)
        if session is None:
            self.window.status_message("No Slynk connection is assigned to this window")
            return

        self.window.status_message(f"Loading ASDF system {system_name}…")
        try:
            # SLY reads each expression before evaluating it, so ASDF must be
            # required in a separate request before reading ASDF-qualified forms.
            await session.slynk.eval("(require :asdf)", False, "CL-USER")
            translator = session.filename_translator
            remote_root = (
                str(project_root)
                if isinstance(translator, IdentityTranslator)
                else translator.local_to_remote(str(project_root))
            )
            result = await session.slynk.eval(
                asdf_load_form(asd_file, remote_root), False, "CL-USER")
        except Exception as error:
            self.window.status_message(f"Failed to load ASDF system {system_name}")
            print(f"SlyLoadAsdfProjectCommandException: {error}")
            return

        self.window.status_message(
            f"Loaded ASDF system {system_name}: {result or 'done'}")
