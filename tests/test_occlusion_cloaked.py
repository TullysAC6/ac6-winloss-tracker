"""Occlusion must ignore windows DWM does not composite, and nothing else.

Real AC6 RC failure this covers: a 1x1 explorer.exe `ThumbnailDeviceHelperWnd`
at (0, 0) reported `IsWindowVisible() == True`, overlapped a borderless
full-screen game whose client also starts at (0, 0), and rejected every effect
screenshot with `reason=occluded, phase=before_capture`.

The only pre-existing occlusion test placed its fake game at +80+80, which
cannot overlap a window at (0, 0), so this class of failure was unreachable.

The wiring tests below decide the cloak answer for every window themselves, so
they do not depend on whatever the host desktop happens to be showing. The
reproduction test at the end deliberately does use the real desktop.
"""
import ctypes
import os
import sys
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import result_detector  # noqa: E402
from result_detector import RECT, WinApi  # noqa: E402

user32 = ctypes.windll.user32
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]


def window_rect(hwnd):
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return {"left": rect.left, "top": rect.top,
            "width": rect.right - rect.left, "height": rect.bottom - rect.top}


@unittest.skipUnless(os.name == "nt", "Win32 occlusion behaviour")
class CloakedWindowOcclusionTests(unittest.TestCase):
    """Real windows and real EnumWindows; only DWM's cloak answer is supplied."""

    def setUp(self):
        import tkinter as tk

        import game_overlay

        self.api = WinApi()
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.geometry("400x300+0+0")
        self.root.configure(bg="#123456")
        self.root.attributes("-topmost", True)
        self.root.update()
        self.game_hwnd = game_overlay._tk_toplevel_hwnd(self.root.winfo_id())
        self.addCleanup(self.root.destroy)
        self.region = window_rect(self.game_hwnd)

        self.blocker = tk.Toplevel(self.root)
        self.blocker.overrideredirect(True)
        self.blocker.geometry(
            f"120x90+{self.region['left'] + 10}+{self.region['top'] + 10}")
        self.blocker.configure(bg="#ff0000")
        self.blocker.attributes("-topmost", True)
        self.blocker.update()
        self.blocker_hwnd = game_overlay._tk_toplevel_hwnd(self.blocker.winfo_id())
        self.addCleanup(self.blocker.destroy)

    def unobscured(self, painted=(), allowed=()):
        """Only `painted` windows are composited; everything else is cloaked.

        Deciding the answer for every window removes the host desktop from the
        result while still exercising the real enumeration and rect logic.
        """
        painted = {int(h) for h in painted}
        with patch.object(WinApi, "is_cloaked",
                          lambda _self, hwnd: int(hwnd) not in painted), \
             patch.object(self.api.user32, "GetForegroundWindow",
                          return_value=self.game_hwnd):
            return self.api.region_unobscured(self.game_hwnd, self.region,
                                              allowed=allowed)

    def test_a_genuine_covering_window_still_blocks(self):
        self.assertFalse(self.unobscured(painted=[self.blocker_hwnd]),
                         "a real painted window over the game must reject capture")

    def test_a_non_composited_overlapping_window_does_not_block(self):
        # Same window, same geometry, same overlap. Only DWM's answer differs.
        self.assertTrue(self.unobscured(painted=[]),
                        "a window DWM does not composite cannot occlude")

    def test_an_allowed_overlay_window_is_still_allowed(self):
        self.assertTrue(
            self.unobscured(painted=[self.blocker_hwnd], allowed=(self.blocker_hwnd,)),
            "Tracker overlay windows passed in `allowed` must not block")

    def test_capture_still_requires_the_game_to_be_foreground(self):
        with patch.object(WinApi, "is_cloaked", lambda _self, hwnd: True), \
             patch.object(self.api.user32, "GetForegroundWindow", return_value=0):
            self.assertFalse(self.api.region_unobscured(self.game_hwnd, self.region),
                             "foreground ownership is still mandatory")

    def test_size_is_not_the_rule(self):
        """A 1x1 window that DWM *does* composite is still a real occluder."""
        import tkinter as tk

        import game_overlay

        pixel = tk.Toplevel(self.root)
        pixel.overrideredirect(True)
        pixel.geometry(f"1x1+{self.region['left']}+{self.region['top']}")
        pixel.configure(bg="#00ff00")
        pixel.attributes("-topmost", True)
        pixel.update()
        self.addCleanup(pixel.destroy)
        pixel_hwnd = game_overlay._tk_toplevel_hwnd(pixel.winfo_id())

        self.assertFalse(self.unobscured(painted=[pixel_hwnd]),
                         "a painted 1x1 window must still block; area is not the test")
        self.assertTrue(self.unobscured(painted=[]),
                        "the same 1x1 window, cloaked, must not block")

    def test_a_window_that_does_not_overlap_never_blocks(self):
        import tkinter as tk

        import game_overlay

        far = tk.Toplevel(self.root)
        far.overrideredirect(True)
        far.geometry(f"80x60+{self.region['left'] + self.region['width'] + 40}"
                     f"+{self.region['top'] + self.region['height'] + 40}")
        far.attributes("-topmost", True)
        far.update()
        self.addCleanup(far.destroy)
        far_hwnd = game_overlay._tk_toplevel_hwnd(far.winfo_id())
        self.assertTrue(self.unobscured(painted=[far_hwnd]))


@unittest.skipUnless(os.name == "nt", "Win32 DWM behaviour")
class IsCloakedTests(unittest.TestCase):
    def setUp(self):
        self.api = WinApi()

    def test_unqueryable_window_fails_closed(self):
        """An unknown answer must be treated as a real, painted window."""
        with patch.object(self.api.dwmapi, "DwmGetWindowAttribute", return_value=1):
            self.assertFalse(self.api.is_cloaked(12345))
        with patch.object(self.api.dwmapi, "DwmGetWindowAttribute",
                          side_effect=OSError("boom")):
            self.assertFalse(self.api.is_cloaked(12345))

    def test_zero_state_is_not_cloaked(self):
        def not_cloaked(_hwnd, _attribute, buffer, _size):
            ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD))[0] = 0
            return 0

        with patch.object(self.api.dwmapi, "DwmGetWindowAttribute", not_cloaked):
            self.assertFalse(self.api.is_cloaked(12345))

    def test_every_documented_cloak_state_counts(self):
        for state in (1, 2, 4):  # DWM_CLOAKED_APP / _SHELL / _INHERITED
            with self.subTest(state=state):
                def cloaked(_hwnd, _attribute, buffer, _size, value=state):
                    ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD))[0] = value
                    return 0

                with patch.object(self.api.dwmapi, "DwmGetWindowAttribute", cloaked):
                    self.assertTrue(self.api.is_cloaked(12345))

    def test_the_constant_matches_the_documented_dwm_attribute(self):
        self.assertEqual(result_detector.DWMWA_CLOAKED, 14)

    def test_the_real_dwm_query_works_here(self):
        seen = {"cloaked": 0, "painted": 0}
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd, _):
            if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
                seen["cloaked" if self.api.is_cloaked(hwnd) else "painted"] += 1
            return True

        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows(callback_type(visit), 0)
        self.assertGreater(seen["painted"], 0, "no windows enumerated at all")


@unittest.skipUnless(os.name == "nt" and os.environ.get("AC6_RUN_SCREENSHOT_INTEGRATION") == "1",
                     "requires an unlocked, interactive Windows desktop")
class BorderlessOriginReproductionTests(unittest.TestCase):
    """The RC failure geometry: a foreground borderless game whose client is (0, 0).

    Uses the real desktop and no cloak stubbing at all. On a desktop carrying a
    degenerate shell window at the origin - which is the normal Windows 11 state
    - this fails before the fix and passes after it.
    """

    def test_a_foreground_full_screen_game_at_the_origin_is_capturable(self):
        import tkinter as tk

        import game_overlay

        api = WinApi()
        kernel32 = ctypes.windll.kernel32
        root = tk.Tk()
        root.overrideredirect(True)
        root.geometry(f"{user32.GetSystemMetrics(0)}x{user32.GetSystemMetrics(1)}+0+0")
        root.configure(bg="#123456")
        root.attributes("-topmost", True)
        root.update()
        hwnd = game_overlay._tk_toplevel_hwnd(root.winfo_id())
        try:
            other = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
            user32.AttachThreadInput(kernel32.GetCurrentThreadId(), other, True)
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                root.focus_force()
                root.update()
            finally:
                user32.AttachThreadInput(kernel32.GetCurrentThreadId(), other, False)
            root.update()
            if int(user32.GetForegroundWindow() or 0) != hwnd:
                self.skipTest("could not take the foreground on this desktop")

            region = window_rect(hwnd)
            self.assertEqual((region["left"], region["top"]), (0, 0))
            self.assertTrue(
                api.region_unobscured(hwnd, region),
                "a foreground borderless game at the origin must be capturable; "
                "a non-composited shell window must not reject it")

            # Treating cloaked windows as occluders is what broke the real run.
            with patch.object(WinApi, "is_cloaked", lambda _self, _hwnd: False):
                unfixed = api.region_unobscured(hwnd, region)
            if unfixed:
                self.skipTest("no degenerate shell window at the origin on this desktop")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
