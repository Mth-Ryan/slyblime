import asyncio
import gc
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class RuntimeCompatibilityTests(unittest.TestCase):
    def test_sexp_parser_imports_and_round_trips_on_current_python(self):
        from src import sexpdata

        form = sexpdata.loads("(cl-user::hello 42 :ok)")
        self.assertEqual(sexpdata.loads(sexpdata.dumps(form)), form)

    def test_dispatcher_async_waiter_builds_event_without_removed_loop_argument(self):
        from src.pydispatch.aioutils import AioEventWaiter

        waiter = AioEventWaiter(object())
        waiter.trigger("payload", code=7)
        self.assertTrue(waiter.aio_event.is_set())
        self.assertEqual((waiter.args, waiter.kwargs), (("payload",), {"code": 7}))

    def test_async_weak_method_cleanup_avoids_weak_dictionary_internals(self):
        from src.pydispatch.aioutils import AioWeakMethodContainer

        class Listener:
            async def callback(self):
                pass

        callbacks = AioWeakMethodContainer()
        listener = Listener()
        callbacks.add_method(object(), listener.callback)
        self.assertEqual(len(callbacks.event_loop_map), 1)

        del listener
        gc.collect()

        self.assertEqual(len(callbacks), 0)
        self.assertEqual(callbacks.event_loop_map, {})

    def test_slynk_repl_process_encodes_channel_message(self):
        from src.slynk.util import Repl

        class Channel:
            id = 4
            is_open = True

            def bind(self, **callbacks):
                self.callbacks = callbacks

            def send_message(self, message):
                self.message = f"(:EMACS-CHANNEL-SEND {self.id} {message})"

        channel = Channel()
        repl = Repl(channel)
        repl.process("(+ 1 2)")
        self.assertIn("(:PROCESS \"(+ 1 2)\")", channel.message)
        self.assertTrue(channel.message.startswith("(:EMACS-CHANNEL-SEND 4 "))

    def test_server_side_close_event_does_not_call_missing_print_method(self):
        from src.slynk.util import Repl

        class Channel:
            id = 1
            is_open = True

            def bind(self, **callbacks):
                self.callbacks = callbacks

            def send_message(self, message):
                pass

        channel = Channel()
        repl = Repl(channel, send_events=True)
        repl.process_message([":SERVER-SIDE-REPL-CLOSE"])
        self.assertFalse(repl.is_open)
        self.assertFalse(channel.is_open)

    def test_informative_weak_dictionary_callback_survives_python_314(self):
        from src.pydispatch.utils import InformativeWVDict

        removed = []
        values = InformativeWVDict(del_callback=removed.append)

        class Value:
            pass

        values["key"] = Value()
        gc.collect()
        self.assertEqual(removed, ["key"])


if __name__ == "__main__":
    unittest.main()
