import unittest


class WalkthroughTest(unittest.TestCase):
    """Runs tools/walkthrough.py (button presses on the real window, fake model and Telegram). Needs a display."""

    def test_every_step_passes(self):
        import tkinter
        try:
            tkinter.Tk().destroy()
        except tkinter.TclError:
            self.skipTest("no display")
        from tools.walkthrough import Walkthrough
        walk = Walkthrough(shots=None, visible=False)
        ok = walk.run()
        failed = [f"{name}: {why}" for name, passed, why in walk.results if not passed]
        self.assertTrue(ok, "; ".join(failed))


if __name__ == "__main__":
    unittest.main()
