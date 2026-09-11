"""Bounded history, cached polling, and real scrollable Tk view."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class DashboardHistoryTests(unittest.TestCase):
    def test_fifty_newest_and_unchanged_poll_preserves_scroll(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            import server
            from history_store import HistoryStore
            from dashboard import HistoryFrame
            store = HistoryStore(Path(directory))
            store.start_session()
            for number in range(61):
                store.record_result(str(number), "win", "test", {"wins": number+1, "losses": 0,
                                    "streak": number+1, "best_streak": number+1}, created_at=number+1)
            with patch.object(server, "history", store), patch.object(server, "dashboard_cache", None), patch.object(
                    store, "recent_matches", wraps=store.recent_matches) as query:
                first = server.dashboard_summary()
                self.assertEqual(len(first["recent_matches"]), 50)
                self.assertEqual([row["event_id"] for row in first["recent_matches"]],
                                 [str(number) for number in range(60, 10, -1)])
                self.assertEqual(first["lifetime"]["matches"], 61)
                for _ in range(20):
                    self.assertEqual(server.dashboard_summary(), first)
                query.assert_called_once_with(50)
                if os.name != "nt":
                    return
                import tkinter as tk
                from tkinter import ttk
                root = tk.Tk()
                root.geometry("800x450")
                try:
                    view = HistoryFrame(root, ttk)
                    view.frame.pack(fill="both", expand=True)
                    view.update(first["recent_matches"])
                    root.update()
                    self.assertEqual(len(view.tree.get_children()), 50)
                    self.assertTrue(view.tree.cget("yscrollcommand"))
                    view.tree.yview_moveto(1.0)
                    root.update()
                    position = view.tree.yview()
                    self.assertGreater(position[0], 0)
                    with patch.object(view.tree, "delete", wraps=view.tree.delete) as delete:
                        view.update(first["recent_matches"])
                        delete.assert_not_called()
                    self.assertEqual(view.tree.yview(), position)
                    self.assertEqual(view.tree.item(view.tree.get_children()[0], "values")[2], "61")
                finally:
                    root.destroy()


if __name__ == "__main__":
    unittest.main()
