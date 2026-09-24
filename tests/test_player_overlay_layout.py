"""UI-1A Player Overlay: value-first layout, safe zone, acknowledgement (issue #25).

T0 classes use fake fonts/canvases and never open a window. ``RealTkCanvasTests``
measures real Tk fonts and the three real overlay HWNDs, so it is owned by T2.
"""
import os
import queue
import re
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import game_overlay  # noqa: E402

SOURCE = (ROOT / "game_overlay.py").read_text(encoding="utf-8")
RESOLUTIONS = ((1920, 1080), (2560, 1440), (3840, 2160), (3440, 1440))  # 16:9 x3, 21:9
SCALES = (1.0, 1.25, 1.5)
PACHINKO_WORDS = ("アツい", "激アツ", "覚醒", "RUSH", "LEGEND")
BEST_WORDS = ("BEST", "最高連勝", "best")


def method_source(name):
    start = SOURCE.index(f"    def {name}(")
    end = SOURCE.find("\n    def ", start + 1)
    return SOURCE[start:end]


class FakeFont:
    """Proportional metrics; ``per_char`` scales with the emulated DPI."""

    def __init__(self, per_char, linespace, ascent):
        self.per_char, self.linespace, self.ascent = per_char, linespace, ascent

    def measure(self, text):
        return round(len(text) * self.per_char)

    def metrics(self, name):
        return {"linespace": self.linespace, "ascent": self.ascent}[name]


class FakeCanvas:
    def __init__(self):
        self.items, self.deleted, self.size, self.recoloured = [], [], None, []

    def configure(self, **kwargs):
        self.size = (kwargs.get("width"), kwargs.get("height"))

    def delete(self, tag):
        self.deleted.append(tag)
        if tag == "all":
            self.items = []

    def create_text(self, x, y, **kwargs):
        self.items.append(("text", (x, y), kwargs))

    def create_line(self, *points, **kwargs):
        self.items.append(("line", points, kwargs))

    def itemconfigure(self, tag, **kwargs):
        self.recoloured.append((tag, kwargs.get("fill")))

    def texts(self, colour=None):
        return [kw["text"] for kind, _, kw in self.items
                if kind == "text" and (colour is None or kw["fill"] == colour)]


class FakeRoot:
    def __init__(self):
        self.scheduled, self.cancelled, self._next = [], [], 0

    def after(self, delay, callback):
        self._next += 1
        self.scheduled.append((delay, callback, f"after#{self._next}"))
        return f"after#{self._next}"

    def after_cancel(self, identifier):
        self.cancelled.append(identifier)


def stats(wins=0, losses=0, streak=0, best=0):
    return game_overlay.normalize_stats(
        {"wins": wins, "losses": losses, "streak": streak, "best_streak": max(best, streak)})


def partial_overlay(scale=1.0, **values):
    """A GameOverlay with only the state _render/_show_at_game read."""
    overlay = game_overlay.GameOverlay.__new__(game_overlay.GameOverlay)
    overlay.tk = Mock(TclError=RuntimeError)
    overlay.root = FakeRoot()
    overlay.canvas = FakeCanvas()
    overlay.text_window = Mock()
    overlay.main_font = FakeFont(16 * scale, round(38 * scale), round(30 * scale))
    overlay.label_font = FakeFont(8 * scale, round(18 * scale), round(14 * scale))
    overlay._ui_scale = scale
    overlay.last_stats = stats(**values)
    overlay._stats_scope = "session"
    overlay._lifetime = None
    overlay._stats_baseline = True
    overlay._ack_active = False
    overlay._ack_after = None
    overlay._last_render_key = None
    overlay._measured = None
    overlay._available_width = None
    overlay._panel_size = (1, 1)
    overlay.x_offset = None
    overlay.y_offset = None
    return overlay


def assert_inside_safe_zone(case, x, y, w, h, left, top, width, height, scale):
    inset_x, inset_y = game_overlay.safe_inset(width, height, scale)
    case.assertGreaterEqual(x - left, inset_x)
    case.assertGreaterEqual(y - top, inset_y)
    case.assertLessEqual(x + w, left + width - inset_x)
    case.assertLessEqual(y + h, top + height - inset_y)
    # AC6 draws the lock-on target name/health bar from the top centre and the
    # speed gauge bottom-left, so the HUD stays in the top-left quadrant.
    case.assertLessEqual(x + w, left + width * 0.5)
    case.assertLessEqual(y + h, top + height * 0.25)


class PlayerMetricsTests(unittest.TestCase):
    def test_exactly_four_value_first_metrics_without_best(self):
        metrics = game_overlay.player_metrics(12, 7, 63.157, 3)
        self.assertEqual(metrics, (("WIN", "12"), ("LOSS", "7"), ("RATE", "63.2%"), ("STREAK", "3")))
        self.assertEqual(game_overlay.player_metrics(0, 0, 0.0, 0)[2], ("RATE", "0.0%"))
        self.assertEqual(game_overlay.player_metrics(5, 0, 100.0, 5)[2], ("RATE", "100.0%"))
        for label, _ in metrics:
            self.assertNotIn("BEST", label)

    def test_underlying_best_statistic_is_unchanged(self):
        normalized = stats(wins=9, losses=2, streak=3, best=7)
        self.assertEqual(normalized["best_streak"], 7)
        # The pre-UI-1A streak status data is still produced; it is simply not drawn.
        self.assertEqual((normalized["status"], normalized["status_level"]), ("アツい", 1))
        overlay = partial_overlay(wins=9, losses=2, streak=3, best=7)
        self.assertEqual(overlay._display_values(), (9, 2, overlay.last_stats["win_rate"], 7, ""))

    def test_font_family_follows_the_approved_fallback_order(self):
        pick = game_overlay.pick_font_family
        self.assertEqual(pick({"Segoe UI Variable Display", "Yu Gothic UI"}, game_overlay.VALUE_FONTS),
                         "Segoe UI Variable Display")
        self.assertEqual(pick({"Yu Gothic UI", "Meiryo"}, game_overlay.VALUE_FONTS), "Yu Gothic UI")
        self.assertEqual(pick(set(), game_overlay.LABEL_FONTS), "Meiryo")


class PlayerRenderTests(unittest.TestCase):
    def test_values_are_primary_and_labels_quieter(self):
        overlay = partial_overlay(wins=12, losses=7, streak=3)
        overlay._render()
        canvas = overlay.canvas
        self.assertEqual(canvas.texts(game_overlay.TEXT_FG), ["12", "7", "63.2%", "3"])
        self.assertEqual(canvas.texts(game_overlay.LABEL_FG), ["WIN", "LOSS", "RATE", "STREAK"])
        self.assertEqual(canvas.texts(game_overlay.ACCENT), ["SESSION"])
        values = [kw for kind, _, kw in canvas.items if kind == "text" and kw["fill"] == game_overlay.TEXT_FG]
        labels = [kw for kind, _, kw in canvas.items if kind == "text" and kw["fill"] == game_overlay.LABEL_FG]
        self.assertTrue(all(kw["font"] is overlay.main_font for kw in values))
        self.assertTrue(all(kw["font"] is overlay.label_font for kw in labels))
        value_y = {pos[1] for kind, pos, kw in canvas.items if kw.get("fill") == game_overlay.TEXT_FG}
        label_y = {pos[1] for kind, pos, kw in canvas.items if kw.get("fill") == game_overlay.LABEL_FG}
        self.assertEqual(len(value_y), 1)
        self.assertLess(max(value_y), min(label_y), "values are the first metric scan line")

    def test_best_and_pachinko_status_never_reach_the_persistent_panel(self):
        for streak in (0, 3, 5, 10, 15, 20, 25, 49, 50):
            with self.subTest(streak=streak):
                overlay = partial_overlay(wins=streak + 1, losses=1, streak=streak, best=streak + 4)
                overlay._render()
                drawn = " ".join(overlay.canvas.texts())
                for word in PACHINKO_WORDS + BEST_WORDS:
                    self.assertNotIn(word, drawn)
                rate = f"{(streak + 1) / (streak + 2) * 100:.1f}%"
                self.assertEqual(overlay.canvas.texts(game_overlay.TEXT_FG),
                                 [str(streak + 1), "1", rate, str(streak)], "BEST value is not drawn")
        render = method_source("_render")
        for token in ('"status"', "status_level", "最高連勝", "STATUS_COLORS", "best_streak}"):
            self.assertNotIn(token, render)

    def test_lifetime_scope_is_labelled_and_streak_stays_session(self):
        overlay = partial_overlay(wins=2, losses=1, streak=2)
        overlay._stats_scope = "lifetime"
        overlay._lifetime = {"wins": 40, "losses": 10, "best_streak": 9, "win_rate": 80.0}
        overlay._render()
        self.assertEqual(overlay.canvas.texts(game_overlay.TEXT_FG), ["40", "10", "80.0%", "2"])
        self.assertEqual(overlay.canvas.texts(game_overlay.ACCENT), ["LIFETIME TOTALS"])

    def test_unchanged_data_does_not_redraw(self):
        overlay = partial_overlay(wins=4, losses=2, streak=1)
        overlay._render()
        overlay._render()
        self.assertEqual(overlay.canvas.deleted, ["all"])
        # BEST and the old streak status are not drawn, so they cannot force a repaint.
        overlay.last_stats = dict(overlay.last_stats, best_streak=99, status="激アツ", status_level=2)
        overlay._render()
        self.assertEqual(overlay.canvas.deleted, ["all"])
        overlay._lifetime_queue = queue.Queue()
        with patch.object(game_overlay, "load_config", return_value={"overlay_stats_scope": "session"}):
            for _ in range(20):  # five seconds of 250 ms ticks
                game_overlay.GameOverlay._drain_display_scope(overlay)
        self.assertEqual(overlay.canvas.deleted, ["all"])
        overlay.last_stats = stats(wins=5, losses=2, streak=2)
        overlay._render()
        self.assertEqual(overlay.canvas.deleted, ["all", "all"])

    def test_columns_do_not_shift_when_a_count_gains_a_digit(self):
        overlay = partial_overlay(wins=9, losses=9, streak=9)
        overlay._render()
        before = [pos[0] for kind, pos, kw in overlay.canvas.items if kw.get("fill") == game_overlay.LABEL_FG]
        overlay.last_stats = stats(wins=10, losses=10, streak=10)
        overlay._render()
        after = [pos[0] for kind, pos, kw in overlay.canvas.items if kw.get("fill") == game_overlay.LABEL_FG]
        self.assertEqual(before, after)

    def test_telemetry_hairlines_are_the_only_accent_items(self):
        overlay = partial_overlay(wins=1)
        overlay._render()
        lines = [kw for kind, _, kw in overlay.canvas.items if kind == "line"]
        self.assertEqual(len(lines), 3, "leading rule plus two corner ticks")
        self.assertTrue(all(kw["tags"] == "accent" and kw["fill"] == game_overlay.ACCENT for kw in lines))
        tagged = [kw for kind, _, kw in overlay.canvas.items if kw.get("tags") == "accent"]
        self.assertEqual(len(tagged), 3)


class LayoutAndSafeZoneTests(unittest.TestCase):
    def test_inset_is_max_of_24_logical_px_and_1_25_percent(self):
        for (width, height) in RESOLUTIONS:
            for scale in SCALES:
                with self.subTest(width=width, height=height, scale=scale):
                    inset_x, inset_y = game_overlay.safe_inset(width, height, scale)
                    self.assertEqual(inset_x, round(max(24 * scale, width * 0.0125)))
                    self.assertEqual(inset_y, round(max(24 * scale, height * 0.0125)))
        self.assertEqual(game_overlay.safe_inset(1920, 1080, 1.0), (24, 24))
        self.assertEqual(game_overlay.safe_inset(3840, 2160, 1.0), (48, 27))
        self.assertEqual(game_overlay.safe_inset(3440, 1440, 1.5), (43, 36))

    def test_matrix_places_panel_inside_client_and_clear_of_hud(self):
        for (width, height) in RESOLUTIONS:
            for scale in SCALES:
                for left, top in ((0, 0), (160, 90)):
                    with self.subTest(width=width, height=height, scale=scale, origin=(left, top)):
                        overlay = partial_overlay(scale, wins=1234, losses=987, streak=25)
                        with patch.object(overlay, "_client_scale", return_value=scale), \
                             patch.object(overlay, "_show_absolute") as show:
                            overlay._show_at_game(left, top, width, height, 1)
                        x, y = show.call_args.args
                        w, h = overlay._panel_size
                        assert_inside_safe_zone(self, x, y, w, h, left, top, width, height, scale)
                        self.assertEqual((x - left, y - top), game_overlay.safe_inset(width, height, scale))

    def test_narrow_client_falls_back_to_8px_gaps_before_type_changes(self):
        columns, caption = (40, 40, 100, 60), 60
        wide = game_overlay.layout_panel(columns, caption, 18, 30, 18, 1.0, None)
        self.assertEqual(wide.gap, game_overlay.GAP_REGULAR)
        compact = game_overlay.layout_panel(columns, caption, 18, 30, 18, 1.0, wide.width - 1)
        self.assertEqual(compact.gap, game_overlay.GAP_COMPACT)
        self.assertEqual(wide.width - compact.width, (24 - 8) * 3)
        self.assertEqual((compact.value_y, compact.label_y, compact.height),
                         (wide.value_y, wide.label_y, wide.height), "type and rows are unchanged")

        overlay = partial_overlay(1.0, wins=12, losses=7, streak=3)
        overlay._render()
        regular_width = overlay._panel_size[0]
        fonts = (overlay.main_font, overlay.label_font)
        with patch.object(overlay, "_client_scale", return_value=1.0), \
             patch.object(overlay, "_show_absolute"):
            overlay._show_at_game(0, 0, regular_width + 2 * 24 - 1, 720, 1)
        self.assertEqual(overlay._last_render_key[2], game_overlay.GAP_COMPACT)
        self.assertEqual((overlay.main_font, overlay.label_font), fonts)

    def test_client_smaller_than_panel_stays_anchored_inside(self):
        x, y = game_overlay.place_panel(100, 50, 200, 60, 352, 87, 1.0)
        self.assertEqual((x, y), (100, 50))

    def test_unchanged_client_does_not_relayout(self):
        overlay = partial_overlay(1.0, wins=3)
        with patch.object(overlay, "_client_scale", return_value=1.0), \
             patch.object(overlay, "_show_absolute"), \
             patch.object(overlay, "_render", wraps=overlay._render) as render:
            for _ in range(40):  # ten seconds of 250 ms positioning ticks
                overlay._show_at_game(0, 0, 1920, 1080, 1)
        self.assertEqual(render.call_count, 1)
        self.assertEqual(overlay.canvas.deleted, ["all"])

    def test_client_dpi_comes_from_the_game_monitor(self):
        overlay = partial_overlay(1.0)
        with patch.object(game_overlay, "_monitor_dpi", return_value=144) as monitor_dpi:
            self.assertEqual(overlay._client_scale(77), 1.5)
            monitor_dpi.assert_called_once_with(77)
        with patch.object(game_overlay, "_monitor_dpi", return_value=0):
            self.assertEqual(overlay._client_scale(77), 1.0, "unknown DPI falls back to the Tk DPI")
        self.assertEqual(game_overlay._monitor_dpi(0), 0)
        # The monitor's effective DPI, never GetDpiForWindow: a DPI-unaware game
        # window reports 96 whatever the Windows scale.
        monitor_dpi_source = SOURCE[SOURCE.index("def _monitor_dpi("):SOURCE.index("def _enable_dpi_awareness(")]
        self.assertIn("GetDpiForMonitor", monitor_dpi_source)
        self.assertIn("MDT_EFFECTIVE_DPI", monitor_dpi_source)
        self.assertNotIn("GetDpiForWindow(", SOURCE)


class ExplicitOffsetCompatibilityTests(unittest.TestCase):
    def test_arguments_default_to_safe_zone_and_keep_explicit_offsets(self):
        args = game_overlay.parse_args(["--process", "armoredcore6.exe"])
        self.assertIsNone(args.x)
        self.assertIsNone(args.y)
        args = game_overlay.parse_args(["--x", "18", "--y", "-40"])
        self.assertEqual((args.x, args.y), (18, -40))
        for bad in (["--x", "5001"], ["--y", "-5001"]):
            with self.subTest(bad=bad), self.assertRaises(SystemExit), \
                 patch("sys.stderr"):
                game_overlay.parse_args(bad)

    def test_explicit_offsets_keep_the_original_fixed_placement(self):
        overlay = partial_overlay(1.0, wins=1)
        overlay.x_offset, overlay.y_offset = 18, 18
        with patch.object(overlay, "_client_scale", side_effect=AssertionError("not consulted")), \
             patch.object(overlay, "_show_absolute") as show:
            overlay._show_at_game(100, 200, 3840, 2160, 1)
        show.assert_called_once_with(118, 218)
        self.assertIsNone(overlay._available_width)

    def test_each_axis_is_independent(self):
        overlay = partial_overlay(1.0, wins=1)
        overlay._render()
        overlay.x_offset = 5
        with patch.object(overlay, "_client_scale", return_value=1.0), \
             patch.object(overlay, "_show_absolute") as show:
            overlay._show_at_game(0, 0, 1920, 1080, 1)
        show.assert_called_once_with(5, 24)
        overlay.x_offset, overlay.y_offset = None, 7
        with patch.object(overlay, "_client_scale", return_value=1.0), \
             patch.object(overlay, "_show_absolute") as show:
            overlay._show_at_game(100, 50, 1920, 1080, 1)
        show.assert_called_once_with(124, 57)

    def test_always_show_preview_origin(self):
        overlay = partial_overlay(1.25)
        self.assertEqual(overlay._preview_origin(), (30, 30))
        overlay.x_offset, overlay.y_offset = 18, 18
        self.assertEqual(overlay._preview_origin(), (18, 18))


class ResultAcknowledgementTests(unittest.TestCase):
    def test_a_new_win_or_lose_pulses_once_and_ends(self):
        overlay = partial_overlay(wins=3, losses=1, streak=1)
        overlay._render()
        for new in (stats(wins=4, losses=1, streak=2), stats(wins=4, losses=2, streak=0)):
            overlay._accept_stats(new)
        self.assertEqual([delay for delay, _, _ in overlay.root.scheduled], [160, 160])
        self.assertEqual(overlay.root.cancelled, ["after#1"], "a second result restarts the pulse")
        self.assertTrue(100 <= game_overlay.ACK_MS <= 200)
        self.assertEqual(overlay.canvas.recoloured[-1], ("accent", game_overlay.ACK_ACCENT))
        overlay.root.scheduled[-1][1]()
        self.assertEqual(overlay.canvas.recoloured[-1], ("accent", game_overlay.ACCENT))
        self.assertFalse(overlay._ack_active)
        self.assertIsNone(overlay._ack_after)

    def test_undo_reset_draw_snapshot_and_first_reading_never_pulse(self):
        overlay = partial_overlay(wins=5, losses=5, streak=0)
        for new in (stats(wins=5, losses=5),            # SSE connect snapshot / DRAW
                    stats(wins=4, losses=5),            # undo
                    stats(wins=0, losses=0)):           # session reset
            overlay._accept_stats(new)
        overlay._stats_baseline = False                 # stats.json was unreadable at start
        overlay._accept_stats(stats(wins=30, losses=10, streak=2))
        self.assertEqual(overlay.root.scheduled, [])
        self.assertTrue(overlay._stats_baseline)
        overlay._accept_stats(stats(wins=31, losses=10, streak=3))
        self.assertEqual(len(overlay.root.scheduled), 1)

    def test_pulse_recolours_hairlines_without_relayout(self):
        overlay = partial_overlay(wins=1)
        overlay._render()
        items_before = list(overlay.canvas.items)
        overlay._acknowledge_result()
        self.assertEqual(overlay.canvas.items, items_before)
        self.assertEqual(overlay.canvas.deleted, ["all"])
        self.assertEqual(overlay.canvas.recoloured, [("accent", game_overlay.ACK_ACCENT)])
        # A repaint while the pulse is live keeps the pulse colour until it ends.
        overlay.last_stats = stats(wins=2)
        overlay._render()
        accents = [kw["fill"] for kind, _, kw in overlay.canvas.items if kw.get("tags") == "accent"]
        self.assertEqual(accents, [game_overlay.ACK_ACCENT] * 3)

    def test_fallback_and_sse_paths_share_the_same_acceptance(self):
        tick = method_source("_tick")
        drain = method_source("_drain_effects")
        self.assertIn("self._accept_stats(fallback_stats)", tick)
        self.assertIn("self._accept_stats(self._stats_queue.get_nowait())", drain)


class MilestoneCharacterizationTests(unittest.TestCase):
    """UI-1A must leave the production milestone set, copy and timing untouched."""

    def test_native_milestone_copy_including_25_is_unchanged(self):
        expected = {
            5: "5連勝　激アツ!!", 10: "10連勝　超激アツ!!", 15: "15連勝　覚醒ゾーン突入",
            20: "20連勝　RUSH突入!!", 25: "25連勝　RUSH継続!!", 30: "30連勝!!", 35: "35連勝!!",
            40: "40連勝!!", 45: "45連勝!!", 50: "50連勝　LEGEND",
        }
        fake_user32 = Mock()
        for milestone, text in expected.items():
            with self.subTest(milestone=milestone):
                overlay = game_overlay.GameOverlay.__new__(game_overlay.GameOverlay)
                overlay.effect_canvas = FakeCanvas()
                overlay.effect_canvas.create_rectangle = Mock()
                overlay.effect_hwnd = 3
                overlay._effect_visible = False
                overlay._active_effect = {"effect_id": str(milestone), "milestone": milestone,
                                          "started": 0.0, "duration": 6.0, "render_key": None}
                with patch.object(game_overlay, "user32", fake_user32, create=True), \
                     patch.object(game_overlay, "HWND_TOPMOST", -1, create=True), \
                     patch.object(game_overlay, "SWP_NOACTIVATE", 0x10, create=True), \
                     patch.object(game_overlay, "SWP_SHOWWINDOW", 0x40, create=True), \
                     patch.object(game_overlay, "SW_SHOWNOACTIVATE", 4, create=True), \
                     patch.object(game_overlay.time, "monotonic", return_value=2.0):
                    overlay._render_milestone_effect((1, 0, 0, 1920, 1080))
                self.assertEqual(overlay.effect_canvas.texts(), [text])

    def test_native_durations_are_the_before_state(self):
        now = time.time()
        for milestone, duration in ((5, 3.5), (25, 3.5), (45, 3.5), (50, 6.0)):
            with self.subTest(milestone=milestone):
                overlay = game_overlay.GameOverlay.__new__(game_overlay.GameOverlay)
                overlay._stats_queue = queue.Queue()
                overlay._effect_queue = queue.Queue()
                overlay._active_effect = None
                overlay._effect_queue.put({"effect_id": f"m{milestone}", "milestone": milestone,
                                           "created_at_ms": int(now * 1000)})
                overlay._drain_effects()
                self.assertEqual(overlay._active_effect["duration"], duration)


class StructuralBudgetTests(unittest.TestCase):
    def test_no_new_thread_process_or_loop(self):
        self.assertEqual(SOURCE.count("threading.Thread("), 1, "only the existing SSE listener")
        self.assertNotRegex(SOURCE, r"\bsubprocess\b|\bPopen\b|multiprocessing")
        timers = re.findall(r"\.after\(([^,]+),", SOURCE)
        self.assertEqual(sorted(timers), sorted(["0", "self.poll_ms", "ACK_MS"]))
        self.assertIn("self._ack_after = self.root.after(ACK_MS, self._end_acknowledgement)", SOURCE)
        self.assertNotIn("after(ACK_MS", method_source("_end_acknowledgement"),
                         "the acknowledgement is one-shot, never a loop")
        self.assertEqual(game_overlay.DEFAULT_POLL_MS, 250)
        self.assertEqual(game_overlay.STATS_FALLBACK_SECONDS, 3.0)

    def test_window_and_lifecycle_contracts_are_intact(self):
        self.assertIn('OVERLAY_MUTEX_NAME = "Local\\\\AC6StatsOverlayV22"', SOURCE)
        self.assertIn("ex |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE", SOURCE)
        for hwnd in ("panel_hwnd", "text_hwnd", "effect_hwnd"):
            self.assertIn(f"self._apply_clickthrough_style(self.{hwnd})", SOURCE)
        self.assertIn('text_window.attributes("-transparentcolor", TRANSPARENT_KEY)', SOURCE)
        self.assertIn("set_ctx(ctypes.c_void_p(-4))", SOURCE, "Per-Monitor-V2 awareness")
        self.assertIn("SWP_NOACTIVATE | SWP_SHOWWINDOW", method_source("_show_absolute"))
        render = method_source("_render")
        self.assertNotIn("effect_canvas", render)
        self.assertNotIn("write_overlay_runtime", render)
        self.assertNotIn("open(", render)

    def test_contrast_of_text_tokens_on_the_panel_base(self):
        def luminance(colour):
            channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        def ratio(a, b):
            high, low = sorted((luminance(a), luminance(b)), reverse=True)
            return (high + 0.05) / (low + 0.05)

        for token in (game_overlay.TEXT_FG, game_overlay.LABEL_FG, game_overlay.ACCENT,
                      game_overlay.ACK_ACCENT):
            with self.subTest(token=token):
                self.assertGreaterEqual(ratio(token, game_overlay.PANEL_BG), 4.5)
                self.assertGreaterEqual(ratio(token, game_overlay.SHADOW), 4.5)


@unittest.skipUnless(os.name == "nt", "real Tk and Win32 overlay windows")
class RealTkCanvasTests(unittest.TestCase):
    """Real fonts at emulated 100/125/150% and the real three-HWND overlay."""

    @classmethod
    def setUpClass(cls):
        # As main() does before any window exists: without it every DPI API
        # reports 96 and a DPI assertion here would hold by construction.
        game_overlay._enable_dpi_awareness()
        with patch.object(game_overlay.GameOverlay, "_tick"), \
             patch.object(game_overlay, "read_stats", return_value=None):
            cls.overlay = game_overlay.GameOverlay("armoredcore6.exe", None, None, 22, 250)
        cls.native_scaling = float(cls.overlay.root.tk.call("tk", "scaling"))

    @classmethod
    def tearDownClass(cls):
        cls.overlay.root.destroy()

    def test_production_default_placement_on_real_windows(self):
        """No --x/--y, real monitor DPI, real SetWindowPos: the L3 review gap."""
        import ctypes
        from ctypes import wintypes
        import tkinter as tk
        overlay = self.overlay
        overlay.root.tk.call("tk", "scaling", self.native_scaling)
        overlay._ui_scale = float(overlay.root.winfo_fpixels("1i")) / 96.0
        overlay._build_fonts(22)
        overlay.last_stats = stats(wins=12, losses=7, streak=3)
        overlay._stats_scope, overlay._lifetime = "session", None
        overlay._available_width = None
        overlay._render()

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(game_overlay.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetDpiForWindow.restype = wintypes.UINT
        user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
        self.assertEqual(user32.GetAwarenessFromDpiAwarenessContext(user32.GetThreadDpiAwarenessContext()),
                         2, "the production Per-Monitor awareness is in force")
        game = tk.Toplevel(overlay.root)
        try:
            game.overrideredirect(True)
            game.geometry("1280x720+64+64")
            game.configure(bg="#203040")
            game.update()
            game_hwnd = game_overlay._tk_toplevel_hwnd(game.winfo_id())
            client, origin = game_overlay.RECT(), game_overlay.POINT(0, 0)
            self.assertTrue(game_overlay.user32.GetClientRect(game_hwnd, ctypes.byref(client)))
            self.assertTrue(game_overlay.user32.ClientToScreen(game_hwnd, ctypes.byref(origin)))
            width, height = client.right - client.left, client.bottom - client.top

            dpi = game_overlay._monitor_dpi(game_hwnd)
            # Independent API: for a window of this Per-Monitor-aware process
            # GetDpiForWindow is the DPI of the monitor it is on.
            self.assertEqual(dpi, user32.GetDpiForWindow(game_hwnd), "effective DPI of the game's monitor")
            scale = dpi / 96.0
            foreground = game_overlay.user32.GetForegroundWindow()
            overlay._show_at_game(origin.x, origin.y, width, height, game_hwnd)
            overlay.root.update()

            inset_x, inset_y = game_overlay.safe_inset(width, height, scale)
            panel_w, panel_h = overlay._panel_size
            for hwnd in (overlay.text_hwnd, overlay.panel_hwnd):
                rect = game_overlay.RECT()
                self.assertTrue(user32.GetWindowRect(hwnd, ctypes.byref(rect)))
                self.assertEqual((rect.left, rect.top), (origin.x + inset_x, origin.y + inset_y))
                self.assertEqual((rect.right - rect.left, rect.bottom - rect.top), (panel_w, panel_h))
                self.assertTrue(game_overlay.user32.IsWindowVisible(hwnd))
            self.assertEqual(game_overlay.user32.GetForegroundWindow(), foreground, "no focus theft")
            assert_inside_safe_zone(self, origin.x + inset_x, origin.y + inset_y, panel_w, panel_h,
                                    origin.x, origin.y, width, height, scale)
        finally:
            overlay._hide()
            game.destroy()
            overlay.root.update()
        self.assertFalse(game_overlay.user32.IsWindowVisible(overlay.text_hwnd))
        self.assertFalse(game_overlay.user32.IsWindowVisible(overlay.panel_hwnd))

    def use_scale(self, scale):
        overlay = self.overlay
        overlay.root.tk.call("tk", "scaling", 96 * scale / 72)
        overlay._ui_scale = float(overlay.root.winfo_fpixels("1i")) / 96.0
        overlay._build_fonts(22)
        return overlay

    def test_real_font_matrix_fits_and_draws_only_the_four_metrics(self):
        cases = ((0, 0, 0, None), (12, 7, 3, None), (99999, 99999, 50, None),
                 (2, 1, 1, {"wins": 12345, "losses": 6789, "best_streak": 9, "win_rate": 64.5}))
        for scale in SCALES:
            overlay = self.use_scale(scale)
            self.assertAlmostEqual(overlay._ui_scale, scale, delta=0.02)
            for wins, losses, streak, lifetime in cases:
                with self.subTest(scale=scale, wins=wins, lifetime=bool(lifetime)):
                    overlay.last_stats = stats(wins=wins, losses=losses, streak=streak)
                    overlay._stats_scope = "lifetime" if lifetime else "session"
                    overlay._lifetime = lifetime
                    overlay._available_width = None
                    overlay._render()
                    canvas = overlay.canvas
                    width, height = int(canvas.cget("width")), int(canvas.cget("height"))
                    self.assertEqual((width, height), overlay._panel_size)
                    for item in canvas.find_all():
                        if canvas.type(item) == "text":  # no glyph may be clipped
                            left, top, right, bottom = canvas.bbox(item)
                        else:  # bbox() pads lines with slop; check the drawn points
                            xs, ys = canvas.coords(item)[0::2], canvas.coords(item)[1::2]
                            left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
                        self.assertGreaterEqual(left, 0)
                        self.assertGreaterEqual(top, 0)
                        self.assertLessEqual(right, width)
                        self.assertLessEqual(bottom, height)
                    texts = [canvas.itemcget(i, "text") for i in canvas.find_all()
                             if canvas.type(i) == "text"]
                    drawn = " ".join(texts)
                    for word in PACHINKO_WORDS + BEST_WORDS:
                        self.assertNotIn(word, drawn)
                    for label in ("WIN", "LOSS", "RATE", "STREAK"):
                        self.assertEqual(texts.count(label), 2, "label plus its shadow")
                    label_points = abs(int(overlay.label_font.actual("size")))
                    self.assertGreaterEqual(label_points, 9, "9 pt = 12 logical px minimum")

    def test_real_panel_sizes_fit_every_resolution_and_scale(self):
        for scale in SCALES:
            overlay = self.use_scale(scale)
            overlay.last_stats = stats(wins=999, losses=999, streak=25)
            overlay._stats_scope, overlay._lifetime = "session", None
            for width, height in RESOLUTIONS:
                with self.subTest(scale=scale, width=width, height=height), \
                     patch.object(overlay, "_client_scale", return_value=scale), \
                     patch.object(overlay, "_show_absolute") as show:
                    overlay._available_width = -1  # force re-evaluation for this client
                    overlay._show_at_game(0, 0, width, height, 1)
                    x, y = show.call_args.args
                    w, h = overlay._panel_size
                    assert_inside_safe_zone(self, x, y, w, h, 0, 0, width, height, scale)
                    self.assertEqual(overlay._last_render_key[2], game_overlay.GAP_REGULAR)

    def test_three_real_click_through_hwnds_are_preserved(self):
        overlay = self.overlay
        hwnds = {overlay.panel_hwnd, overlay.text_hwnd, overlay.effect_hwnd}
        self.assertEqual(len(hwnds), 3)
        required = (game_overlay.WS_EX_LAYERED | game_overlay.WS_EX_TRANSPARENT
                    | game_overlay.WS_EX_TOOLWINDOW | game_overlay.WS_EX_NOACTIVATE)
        for hwnd in hwnds:
            self.assertEqual(game_overlay._get_exstyle(hwnd) & required, required)
            self.assertFalse(game_overlay.user32.IsWindowVisible(hwnd), "hidden until AC6 is foreground")
        self.assertEqual(str(overlay.text_window.attributes("-transparentcolor")), game_overlay.TRANSPARENT_KEY)
        self.assertAlmostEqual(float(overlay.panel_root.attributes("-alpha")),
                               game_overlay.DEFAULT_PANEL_OPACITY / 100, places=2)


if __name__ == "__main__":
    unittest.main()
