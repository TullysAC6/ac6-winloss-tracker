from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
dashboard = (ROOT / "dashboard.py").read_text(encoding="utf-8")
launcher = (ROOT / "launcher.pyw").read_text(encoding="utf-8")
installer = (ROOT / "install.ps1").read_text(encoding="utf-8")
requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
server = (ROOT / "server.py").read_text(encoding="utf-8")

assert 'ttk.Window(themename="darkly")' in dashboard
assert 'self.root.option_add("*Font", "Segoe UI 10")' not in dashboard
assert 'tkfont.nametofont(font_name).configure(family="Segoe UI", size=10)' in dashboard
assert 'self.root.geometry("950x650")' in dashboard
assert 'self.root.minsize(800, 540)' in dashboard
assert 'attributes("-topmost"' not in dashboard
assert "threading.Thread(target=self._worker" in dashboard
assert "POLL_SECONDS = 1.0" in dashboard
assert 'shell=False' in launcher and 'DASHBOARD_PATH = APP_DIR / "dashboard.py"' in launcher
assert 'DASHBOARD_LOG = DATA_DIR / "dashboard.log"' in launcher
assert 'dashboard runtime verification: success' in launcher
assert 'runtime["pid"] == process.pid' in launcher
assert "ダッシュボードを開く" in launcher
assert "ttkbootstrap==2.2.2" in requirements
assert "import mss, ttkbootstrap" in installer
assert "dashboard\\.py" in installer
assert 'if path == "/api/dashboard/summary":' in server
assert '"dashboard": dashboard' in server
print("dashboard UI/polling/launcher/installer/optional-health static checks: OK")
