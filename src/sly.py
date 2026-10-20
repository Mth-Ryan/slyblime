from sublime import *
import sublime_plugin, threading, asyncio  # import the required modules

from operator import itemgetter

from . import util, sexpdata, debugger, filename_translation
from .debugger import Debugger
from .slynk import slynk
from .tracer import Tracer
from .inferior_startup import connect_when_ready, wait_for_startup
import logging
import functools
from socket import socket
import shlex
from typing import *

_settings = None

SessionId = int
WindowId = int

def settings():
    global _settings
    if not ("_settings" in globals() and globals()["_settings"]):
        _settings = load_settings("sly.sublime-settings")
    return _settings


class SlynkSession:
    """
    This object stores all the information the plugin will use to connect 
    and deal with (a single) slynk.

    Inspectors, REPLs etc are all stored here
    """
    def __init__(self, host, port, window, loop) -> None:
        self.slynk = slynk.SlynkClient(host, port)
        self.window = window
        self.repl_views = {}
        self.loop = loop
        self.slynk.bind(__aio_loop__=loop,
                        connect=self.on_connect,
                        disconnect=self.on_disconnect,
                        debug_setup=self.on_debug_setup,
                        debug_activate=self.on_debug_activate,
                        debug_return=self.on_debug_return,
                        read_from_minibuffer=self.on_read_from_minibuffer,
                        y_or_n_p=self.on_y_or_n)
        self.inspectors = {}
        self.nearest_inspector = None
        self.debuggers = {}
        self.tracer = None
        self.id: SessionId = None
        self.autoclose = False
        self.process = None
        self.filename_translator = filename_translation.IdentityTranslator()

    async def connect(self):
        slynk = self.slynk
        self.window.status_message(f"Attempting to connect to Slynk at {slynk.host}:{slynk.port} [≈ 1 min]")
        if self.process:
            await connect_when_ready(slynk, self.loop)
        else:
            await slynk.connect(self.loop)
        local_package_path = (
            f"{packages_path()}/{__name__.split('.')[0]}" if self.process else None
        )
        await slynk.prepare(local_package_path)
        self.filename_translator = await filename_translation.get_translator(self.window, self)
        set_timeout(
            lambda: self.window.run_command("sly_create_repl"),
            10)

        #await slynk.closed()

    def on_connect(self, *args):
        self.window.status_message("Slynk connexion established")
        print("Slynk connexion established")

    def on_disconnect(self, *args):
        self.window.status_message("Slynk connexion lost")
        print("Slynk connexion lost")
        if self.autoclose and self.process:
            try:
                self.process.terminate()
                self.window.status_message("Inferior lisp terminated")
            except Exception as e:
                self.window.status_message(f"Failed to terminate process, now orphaned: {e}")
                sessions.orphaned_inferior_lisps.append(self)
        elif self.process:
            sessions.orphaned_inferior_lisps.append(self)
        for repl_view in self.repl_views.values():
            repl_view.close()


    async def on_debug_setup(self, data):
        if data.thread in self.debuggers:
            debugger = self.debuggers[data.thread]
        else:
            debugger = Debugger(self.window, self, data.thread)
        debugger.update(data)

        (action, index) = await debugger.show(self, data)
        if action == "restart":
            await self.slynk.debug_invoke_restart(data.level, index, data.thread)
        elif action == "restart-frame":
            await self.slynk.debug_restart_frame(index, data.thread)
        #(action, index)

    def on_debug_activate(self, data):
        debugger = self.debuggers[data.thread]
        if not debugger.is_open:
            debugger.reöpen(self.window)
        debugger.focus()

    def on_debug_return(self, data):
        self.debuggers[data.thread].returned(data)

    async def on_read_from_minibuffer(self, prompt, initial_value, future):
        initial_value = initial_value if initial_value else ""
        try:
            output = await util.show_input_panel(self.loop, self.window, prompt, initial_value)
        except asyncio.CancelledError:
            future.cancel()
        else:
            future.set_result(output)

    async def on_y_or_n(self, prompt, future):
        value = yes_no_cancel_dialog(prompt)
        if value == DIALOG_CANCEL:
            future.cancel()
        else:
            future.set_result(True if value == DIALOG_YES else False)


class ConnectSlynkCommand(sublime_plugin.WindowCommand):
    def run(self, **kwargs):  # implement run method
        global loop
        ensure_loop_running()
        asyncio.run_coroutine_threadsafe(
            self.async_run(**kwargs),
            loop)

    async def async_run(self, host=None, port=None, prompt_connexion=None):
        defaults = settings().get("default_connexion_parameters")
        host = defaults["hostname"] if host is None else host
        port = defaults["port"] if port is None else port

        if prompt_connexion in ["both", "host", "hostname"] or host is None:
            host = await util.show_input_panel(
                loop, self.window, "Enter hostname", host)
        if prompt_connexion in ["both", "port"] or port is None:
            port = await util.show_input_panel(
                loop, self.window, "Enter port", str(port))
        port = int(port)

        session = SlynkSession(host, port, self.window, loop)
        await session.connect()
        sessions.add(session)
        sessions.set_by_window(self.window, session)


class SlyStartLispCommand(sublime_plugin.WindowCommand):
    def run(self, **kwargs): 
        global loop
        ensure_loop_running()
        asyncio.run_coroutine_threadsafe(
            self.async_run(**kwargs),
            loop)

    async def async_run(self, **kwargs):  # implement run method
        session = None
        output_task = None
        try:
            params = settings().get("inferior_lisp_process")
            command = params["command"]
            if isinstance(command, str):
                command = shlex.split(command, posix=False)
                command = [
                    argument[1:-1]
                    if len(argument) >= 2
                    and argument[0] == argument[-1]
                    and argument[0] in ("'", '"')
                    else argument
                    for argument in command
                ]
            if not command:
                raise ValueError("The inferior_lisp_process.command setting is empty")

            # Reserve a port, then pass the executable and arguments directly to
            # Windows instead of asking a shell to reinterpret them.
            with socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]

            session = SlynkSession("127.0.0.1", port, self.window, loop)
            session.autoclose = params.get("autoclose", True)
            session.process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output_task = asyncio.create_task(self.log_all(session.process))

            loader_path = (
                f"{packages_path()}/{__name__.split('.')[0]}"
                "/sly/slynk/slynk-loader.lisp"
            )
            boot_script = (
                f"(load {sexpdata.dumps(loader_path)})\n"
                "(setf slynk-loader:*fasl-directory* "
                "(merge-pathnames "
                "(make-pathname :directory "
                "(append '(:relative \".slyblime\" \"fasl\") "
                "(list (format nil \"~A-~A-~A\" "
                "(lisp-implementation-type) (lisp-implementation-version) "
                "(machine-type))))) "
                "(user-homedir-pathname)))\n"
                "(slynk-loader:init :delete nil :reload nil)\n"
                "(setf slynk::*slynk-wire-protocol-version* "
                "(string-right-trim '(#\\Return #\\Newline) "
                "slynk::*slynk-wire-protocol-version*))\n"
                f"(slynk:create-server :port {port} "
                f":dont-close {'nil' if session.autoclose else 't'})\n"
            )
            session.process.stdin.write(boot_script.encode("utf-8"))
            await session.process.stdin.drain()

            await wait_for_startup(session.connect(), session.process, timeout=60)
            sessions.add(session)
            sessions.set_by_window(self.window, session)
        except Exception as e:
            self.window.status_message(f"Failed to start Lisp process: {e}")
            print(f"Failed to start Lisp process: {e}")
            if session and session.process and session.process.returncode is None:
                try:
                    session.process.terminate()
                except ProcessLookupError:
                    pass
            if session and session.process:
                await session.process.wait()
            if output_task:
                await output_task

    async def log_all(self, p):
        while True:
            line = await p.stdout.readline()
            if not line:
                return
            print(line.decode("utf-8", errors="replace"), end="")


class Sessions:
    """
    Singleton object to store all the currently active sessions.
    Designed to allow a natural number of windows to be connected to one session.
    """
    def __init__(self, sessions=None, window_assignment=None):
        self.sessions = {} if sessions is None else sessions
        self.window_assignment = {} if window_assignment is None else window_assignment
        self.next_uid: SessionId = 0
        self.orphaned_inferior_lisps = []

    @property
    def list(self):
        return list(self.sessions.values())

    def add(self, session):
        self.sessions[self.next_uid] = session
        session.id = self.next_uid
        self.next_uid += 1

    def remove(self, session):
        self.mutated = True
        del self.sessions[session.id]
        # It is forbidden to modify a dictionary during iteration
        copy = self.window_assignment.copy()
        for window_id, (window, window_session) in copy.items():
            if window_session is session:
                del self.window_assignment[window_id]
                try:
                    if view := window.active_view(): 
                        view.set_status("sly", "")
                    window.status_message("Slynk session disconnected and unassigned")
                except Exception as e:
                    print(f"Error with status message: {e}")

    def get_by_window_id(self, id: WindowId):
        return self.window_assignment[id][1] if id in self.window_assignment else None

    # This function also performs a lot of UX work the one above doesn't
    # autoset is actually True by default, None means consult user settings.
    def get_by_window(self, window, indicate_failure=True, autoset: bool=None):
        if autoset is None:
            try:
                autoset = settings().get("autoset_slynk_connexion")
            except Exception as e:
                autoset = True
        id: WindowId = window.id()
        if id in self.window_assignment:
            return self.window_assignment[id][1]
        # The session desired is obvious if only one is there
        elif autoset and len(self.sessions) == 1:
            default = self.list[0]
            self.set_by_window(window, default, False)
            window.status_message("Automatically assigned only slynk connexion to window")
            return default
        else:
            if indicate_failure:
                window.status_message(
                    "Slynk not connected" if len(self.sessions) == 0
                                          else "No slynk connexion assigned to window")
            return None

    def set_by_window(self, window, session, message=True):
        self.window_assignment[window.id()] = (window, session)
        if message:
            window.status_message("Slynk assigned to window")

    def window_ids_for_session(self, session) -> List[WindowId]:
        return [window_id for window_id, (__, window_session) in self.window_assignment.items()
                          if window_session == session]

    def windows_for_session(self, session) -> List[Window]:
        return [window for __, (window, window_session) in self.window_assignment.items()
                       if window_session == session]

    def get_by_id(self, session_id: SessionId) -> SlynkSession:
        return self.sessions[session_id]

if "sessions" not in globals():
    sessions = Sessions()
if "loop" not in globals() or loop.is_closed():
    loop = asyncio.new_event_loop()
loop_thread = globals().get("loop_thread")
_loop_lock = threading.Lock()


def ensure_loop_running():
    global loop_thread
    with _loop_lock:
        if loop.is_running() or (loop_thread and loop_thread.is_alive()):
            return
        loop_thread = threading.Thread(
            target=loop.run_forever,
            name="Slyblime asyncio loop",
            daemon=True,
        )
        loop_thread.start()


def plugin_loaded():
    if settings().get("debug"):
        loop.set_debug(True)
        logging.basicConfig(level=logging.DEBUG)


def plugin_unloaded():
    active_sessions = list(sessions.sessions.values())

    async def shutdown():
        for session in active_sessions:
            try:
                session.slynk.disconnect()
            except Exception as error:
                print(f"Error disconnecting Slynk during plugin unload: {error}")
            if session.autoclose and session.process:
                try:
                    session.process.terminate()
                except Exception as error:
                    print(f"Error stopping inferior Lisp during plugin unload: {error}")
        await asyncio.sleep(0)
        loop.stop()

    if loop.is_running():
        asyncio.run_coroutine_threadsafe(shutdown(), loop)
        if loop_thread and loop_thread is not threading.current_thread():
            loop_thread.join(timeout=1)
    sessions.sessions.clear()
    sessions.window_assignment.clear()

class SlyExitEventListener(sublime_plugin.EventListener):
    def on_init(self, views):
        if len(sessions.sessions) != 0:
            return
        for view in views:
            if view.settings().get("sly-open-repl"):
                print("Found sly open repl")
                view.settings().set("sly-open-repl", False)
                view.set_name("🏁" + view.name())
                view.run_command("sly_insert_text",
                    {"pos": len(view),
                     "text": "\n[🏁 Connexion terminated by Sublime Text exit.]"})
                view.set_status("sly", "❌ " + view.settings().get("sly-repl-status"))
