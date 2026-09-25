"""UI-1B: the OBS Broadcast Overlay (overlay.html) and its one Broadcast-only setting.

T0  BroadcastStaticTests   overlay.html / server.py source contracts
    BroadcastDomTests      the overlay's own script under node (tests/test_broadcast_overlay.js)
    BroadcastConfigTests   the /config Broadcast preference, in-process, no socket
T2  BroadcastGeometryTests real layout in a headless Chromium browser (Chrome or Edge),
                           owned through a kill-on-close job on Windows
"""
import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

KEY = "broadcast_show_best_streak"
PLAYER_KEY = "player_streak_status_enabled"
OVERLAY = (ROOT / "overlay.html").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")
# The main commit UI-1B builds on; its overlay.html is the "before" layout.
PREVIOUS_VERSION = "7cc8ebe4b4768ce6318b283d68a34b3697f26702"


def css():
    return OVERLAY[OVERLAY.index("<style>"):OVERLAY.index("</style>")]


def rule(selector):
    match = re.search(r"(?m)^" + re.escape(selector) + r"\{([^}]*)\}", css())
    if match is None:
        raise AssertionError(f"no CSS rule {selector}")
    return match.group(1)


class BroadcastStaticTests(unittest.TestCase):
    def test_existing_copy_is_unchanged(self):
        for template in ("WIN ${s.wins}　LOSE ${s.losses}", "　勝率 ${Number(s.win_rate).toFixed(1)}%",
                         "　連勝 ${s.streak}", "${s.scope_label||\"\"}最高連勝 ${s.best_streak}",
                         'scope_label:"累計　"', "勝敗自動検出 停止", "戦績データ異常", "設定ファイル異常"):
            self.assertIn(template, OVERLAY)

    def test_primary_typography_and_default_placement(self):
        panel = rule("#stats-panel")
        self.assertIn("font-size:24px", panel)
        self.assertIn("font-weight:800", panel)
        self.assertIn("margin-top:4px", panel)
        bar = rule("#top-bar")
        # 18 + 4 = the panel's existing 22 px top; left stays 22 px.
        for declaration in ("position:fixed", "left:22px", "top:18px", "right:18px", "flex-wrap:wrap"):
            self.assertIn(declaration, bar)
        self.assertIn("background:transparent", rule("html,body"))
        self.assertIn("background:rgba(0,0,0,.50)", panel, "panel backing unchanged")

    def test_ordinary_status_is_static_and_only_the_milestone_animates(self):
        style = css()
        self.assertNotIn("infinite", OVERLAY)
        self.assertEqual(re.findall(r"@keyframes\s+([\w-]+)", style), ["milestone"])
        animated = [line for line in style.splitlines() if re.search(r"\banimation\s*:", line)]
        self.assertEqual(len(animated), 1)
        self.assertTrue(animated[0].startswith("#milestone .inner{"))
        self.assertIn("animation:milestone 1.8s ease-out forwards", animated[0])
        for level in range(1, 6):
            self.assertNotIn("animation", rule(f"#stats-panel.hot-{level} .status"))
        for costly in ("requestAnimationFrame", "backdrop-filter", "@import", "url(", "<link", " src=",
                       "<canvas", "filter:blur"):
            self.assertNotIn(costly, OVERLAY)

    def test_the_milestone_path_is_byte_for_byte_unchanged(self):
        for line in (
            'function milestoneText(n){return({5:"5連勝　激アツ!!",10:"10連勝　超激アツ!!",'
            '15:"15連勝　覚醒ゾーン突入",20:"20連勝　RUSH突入!!",25:"25連勝　RUSH継続!!",30:"30連勝!!",'
            '35:"35連勝!!",40:"40連勝!!",45:"45連勝!!",50:"50連勝　LEGEND"})[n]||`${n}連勝!!`}',
            'function flashMilestone(n){if(!statsEnabled||!n)return;if(milestoneTimer)clearTimeout(milestoneTimer);'
            'milestone.className="";milestone.id="milestone";milestone.classList.add(`effect-${Number(n)}`);'
            'milestoneInner.textContent=milestoneText(Number(n));milestone.classList.add("show");'
            'milestoneTimer=setTimeout(()=>{milestone.classList.remove("show");milestone.className="";'
            'milestone.id="milestone";milestoneTimer=null},3000)}',
            "const overlayStartedAt=Date.now(),effectTtlMs=3000;",
            "       !Number.isInteger(created)||created<overlayStartedAt||age<0||age>=effectTtlMs||\n"
            "       seenEffects.has(p.effect_id)||created<lastEffectCreatedAt)return;",
        ):
            self.assertIn(line, OVERLAY)

    def test_same_transport_and_cadence(self):
        self.assertEqual(OVERLAY.count("setInterval("), 1)
        self.assertIn("},3000);", OVERLAY[OVERLAY.index("setInterval("):])
        self.assertEqual(OVERLAY.count('new EventSource("/events")'), 1)
        self.assertEqual(OVERLAY.count('fetch("/config"'), 1)
        self.assertEqual(len(re.findall(r"<script\b", OVERLAY)), 1)

    def test_best_follows_only_an_explicit_false_from_config(self):
        self.assertIn("showBest=c.broadcast_show_best_streak!==false", OVERLAY)
        self.assertNotIn(PLAYER_KEY, OVERLAY)
        self.assertNotIn("preferences", OVERLAY)

    def test_server_adds_one_allow_listed_field_and_no_thread_or_route(self):
        self.assertEqual(SERVER.count("threading.Thread("), 2, "same resident thread topology")
        self.assertEqual(SERVER.count('if path == "'), 11, "no new endpoint")
        self.assertIn('BROADCAST_PREFERENCE_KEYS = ("broadcast_show_best_streak",)', SERVER)
        self.assertNotIn(PLAYER_KEY, SERVER)
        handler = SERVER[SERVER.index('if path == "/config":'):SERVER.index('if path == "/stats":')]
        self.assertLess(handler.index("load_config()"), handler.index("**broadcast_preferences()"),
                        "an invalid config.json fails /config exactly as before")
        self.assertEqual(SERVER.count("**broadcast_preferences()"), 1, "published only through /config")


def find_node():
    return os.environ.get("AC6_TEST_NODE") or shutil.which("node")


class BroadcastDomTests(unittest.TestCase):
    def test_overlay_script_behaviour(self):
        node = find_node()
        if node is None:
            self.assertFalse(os.environ.get("CI"), "Node.js is required to execute the overlay.html regression")
            self.skipTest("Node.js not found; set AC6_TEST_NODE to run the overlay.html regression")
        result = subprocess.run([node, str(ROOT / "tests" / "test_broadcast_overlay.js")], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("UI-1B Broadcast Overlay executable behaviour: OK", result.stdout)


class BroadcastConfigTests(unittest.TestCase):
    def setUp(self):
        import config_utils
        import preferences
        import server
        self.server, self.preferences = server, preferences
        directory = tempfile.TemporaryDirectory(prefix="ac6-ui1b-config-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "preferences.json"
        for patcher in (patch.object(config_utils, "CONFIG_PATH", Path(directory.name) / "config.json"),
                        patch.dict(server.broadcast_preferences_last, {KEY: True}),
                        patch("builtins.print")):
            patcher.start()
            self.addCleanup(patcher.stop)
        preferences._cache = None
        self.addCleanup(setattr, preferences, "_cache", None)

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        self.preferences._cache = None
        return self.path.read_bytes()

    def test_missing_file_means_shown_and_only_the_broadcast_key_is_published(self):
        self.assertEqual(self.server.broadcast_preferences(), {KEY: True})
        self.assertFalse(self.path.exists())

    def test_saved_values_and_the_ui_1a_file(self):
        self.write('{"preferences_version": 2, "broadcast_show_best_streak": false}')
        self.assertEqual(self.server.broadcast_preferences(), {KEY: False})
        self.write('{"preferences_version": 2, "broadcast_show_best_streak": true, '
                   '"player_streak_status_enabled": false}')
        self.assertEqual(self.server.broadcast_preferences(), {KEY: True})
        self.write('{"preferences_version": 1, "player_streak_status_enabled": false}')
        self.assertEqual(self.server.broadcast_preferences(), {KEY: True}, "a UI-1A file: the default")

    def test_a_bad_file_keeps_the_last_value_and_is_never_rewritten(self):
        self.write('{"preferences_version": 2, "broadcast_show_best_streak": false}')
        self.assertEqual(self.server.broadcast_preferences(), {KEY: False})
        for bad in ("{broken", '{"preferences_version": 4, "broadcast_show_best_streak": true}',
                    '{"preferences_version": 2, "broadcast_show_best_streak": "true"}',
                    '{"preferences_version": 2, "broadcast_overlay": {"show_best_streak": true}}',
                    '{"preferences_version": 2, "surprise": true}'):
            with self.subTest(bad=bad):
                before = self.write(bad)
                self.assertEqual(self.server.broadcast_preferences(), {KEY: False})
                self.assertEqual(self.path.read_bytes(), before)

    def test_an_unexpected_reader_failure_never_reaches_config(self):
        self.write('{"preferences_version": 2, "broadcast_show_best_streak": false}')
        self.assertEqual(self.server.broadcast_preferences(), {KEY: False})
        with patch.object(self.preferences, "effective", side_effect=RuntimeError("boom")):
            self.assertEqual(self.server.broadcast_preferences(), {KEY: False})
        with patch.object(self.preferences, "effective", return_value="yes"):
            self.assertEqual(self.server.broadcast_preferences(), {KEY: False}, "only a boolean is published")


# ---------------------------------------------------------------------- T2

BROWSER_TIMEOUT_SECONDS = 120
SIZES = ((1920, 1080), (1280, 720), (2560, 1440), (3840, 2160), (800, 600), (640, 360))
WIDE = ((1920, 1080), (1280, 720), (2560, 1440), (3840, 2160))
# Sources too short for the warnings to drop below the panel (a strip cropped
# to the panel, say): the warnings keep their previous top-right place instead.
SHORT = ((800, 150), (640, 200), (1280, 120), (400, 299))
CONFIG = {"stats_enabled": True, "effect_enabled": True, "overlay_stats_scope": "lifetime",
          "config_health": {"status": "degraded"}}
# A realistic long panel: lifetime counts, the longest status word, both
# warnings that can show while the panel is visible.
STATS = {"wins": 12, "losses": 7, "win_rate": 63.2, "streak": 21, "best_streak": 21,
         "status": "RUSH継続中", "status_level": 5}
LIFETIME = {"wins": 1234, "losses": 987, "win_rate": 55.6, "best_streak": 21}
STATUS = {1: "アツい", 2: "激アツ", 3: "超激アツ", 4: "覚醒ゾーン", 5: "RUSH継続中"}
WARNINGS = [["detector", {"status": "degraded"}]]
# The most common state: session totals, no streak status, no warning.
QUIET = {"config": {"stats_enabled": True, "effect_enabled": True, "overlay_stats_scope": "session",
                    "config_health": {"status": "active"}},
         "stats": {"wins": 3, "losses": 2, "win_rate": 60.0, "streak": 1, "best_streak": 2, "status": "",
                   "status_level": 0}, "events": []}

STUB = """<script>
window.__case=%s;window.__cfg=window.__case.config;window.__es=null;
window.fetch=async()=>({ok:true,json:async()=>window.__cfg});
window.EventSource=class{constructor(){this.l={};window.__es=this}addEventListener(n,f){this.l[n]=f}};
</script>"""
# Runs synchronously while the frame parses: the stubbed /config resolves in the
# microtask checkpoint after the overlay's own script, so its EventSource exists
# here, and the result reaches the parent before the parent finishes loading.
PROBE = """<script>(function(){
const C=window.__case;
const report=text=>{try{parent.document.getElementById("results").append(text+"\\n")}catch(e){parent.postMessage(text,"*")}};
if(!window.__es){report(JSON.stringify({name:C.name,error:"no EventSource after the overlay script"}));return}
const L=window.__es.l,send=(n,d)=>L[n]({data:JSON.stringify(d)});
send("stats",C.stats);send("lifetime",C.lifetime);for(const [n,d] of C.events)send(n,d);
{
 const shown=e=>e&&getComputedStyle(e).display!=="none";
 const box=e=>{if(!shown(e))return null;const b=e.getBoundingClientRect();return [b.left,b.top,b.right,b.bottom]};
 const p=document.getElementById("stats-panel"),q=s=>p.querySelector(s),cs=getComputedStyle(p);
 const out={name:C.name,vw:innerWidth,vh:innerHeight,panel:box(p),sub:box(q(".sub")),
  warnings:["detector-warning","stats-warning","config-warning"].map(i=>box(document.getElementById(i))),
  font:[cs.fontSize,cs.fontWeight,getComputedStyle(q(".record")).fontSize],
  background:[getComputedStyle(document.documentElement).backgroundColor,getComputedStyle(document.body).backgroundColor],
  text:[".record",".rate",".streak",".status",".sub"].map(s=>q(s).textContent),animations:[]};
 for(const level of [0,1,2,3,4,5]){send("stats",Object.assign({},C.stats,{status_level:level,status:C.status[level]||""}));
  out.animations.push([level,getComputedStyle(q(".status")).animationName,document.getAnimations().length]);}
 report(JSON.stringify(out).replace(/[\\u0080-\\uffff]/g,c=>"\\\\u"+c.charCodeAt(0).toString(16).padStart(4,"0")));
}})();</script>"""
PAGE = """<!doctype html><html><body style="margin:0"><pre id="results"></pre>%s<script>
addEventListener("message",e=>document.getElementById("results").append(e.data+"\\n"));</script></body></html>"""


def find_browser():
    explicit = os.environ.get("AC6_TEST_BROWSER")
    if explicit:
        return explicit if Path(explicit).is_file() else None
    candidates = []
    if os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                candidates += [Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe",
                               Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"]
    else:
        candidates += [shutil.which(name) for name in ("google-chrome", "chromium", "chromium-browser")]
    return next((str(path) for path in candidates if path and Path(path).is_file()), None)


def overlay_document(source, case):
    stub = STUB % json.dumps(case, ensure_ascii=True)
    document = source.replace("<script>", stub + "<script>", 1)
    return document.replace("</body>", PROBE + "</body>", 1)


def previous_overlay():
    """overlay.html as it was before UI-1B, from git; fetched if the clone is shallow."""
    git = ["git", "-C", str(ROOT), "-c", f"safe.directory={ROOT}"]
    show = git + ["show", f"{PREVIOUS_VERSION}:overlay.html"]
    result = subprocess.run(show, capture_output=True, timeout=60)
    if result.returncode != 0:
        subprocess.run(git + ["fetch", "--no-tags", "--depth=1", "origin", PREVIOUS_VERSION],
                       capture_output=True, timeout=180)
        result = subprocess.run(show, capture_output=True, timeout=60)
    return result.stdout.decode("utf-8") if result.returncode == 0 else None


def _resume_suspended(pid):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    handle = kernel.OpenProcess(0x0800, False, pid)  # PROCESS_SUSPEND_RESUME
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        status = ntdll.NtResumeProcess(handle)
        if status:
            raise OSError(f"NtResumeProcess failed: {status & 0xFFFFFFFF:#x}")
    finally:
        kernel.CloseHandle(handle)


def run_owned(command):
    """Run the browser; return (returncode, stdout, leftover processes).

    Windows: the browser starts suspended inside a kill-on-close job, so every
    process it starts is owned; anything left after exit is reported, then reaped.
    Elsewhere: its own session / process group, reported and killed the same way.
    """
    if os.name == "nt":
        from t1.process import WorkerJob
        job = WorkerJob()
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=0x00000004 | 0x08000000)  # SUSPENDED | NO_WINDOW
            try:
                job.assign(process.pid)
                _resume_suspended(process.pid)
                stdout, _ = process.communicate(timeout=BROWSER_TIMEOUT_SECONDS)
            except BaseException:
                job.terminate()
                process.communicate(timeout=30)
                raise
            leftover = job.members() if job.wait_empty(15) else []
            if leftover:
                job.terminate()
                job.wait_empty(10)
        finally:
            job.close()
        return process.returncode, stdout, leftover
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, _ = process.communicate(timeout=BROWSER_TIMEOUT_SECONDS)
    except BaseException:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=30)
        raise
    leftover = []
    deadline = time.monotonic() + 15
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        if time.monotonic() >= deadline:
            leftover = [f"process group {process.pid}"]
            os.killpg(process.pid, signal.SIGKILL)
            break
        time.sleep(0.05)
    return process.returncode, stdout, leftover


def render(cases, sources):
    """Lay out every case in an exactly sized iframe in one browser run."""
    browser = find_browser()
    frames = "".join(
        '<iframe style="display:block;border:0;width:%dpx;height:%dpx" srcdoc="%s"></iframe>'
        % (case["width"], case["height"], html.escape(overlay_document(sources[case["source"]], case), quote=True))
        for case in cases)
    directory = tempfile.mkdtemp(prefix="ac6-ui1b-browser-")
    try:
        page = Path(directory) / "page.html"
        page.write_text(PAGE % frames, encoding="utf-8")
        profile = Path(directory) / "profile"
        command = [browser, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-first-run",
                   "--no-default-browser-check", "--disable-extensions", "--disable-background-networking",
                   "--disable-component-update", "--disable-sync", "--disable-breakpad", "--mute-audio",
                   "--hide-scrollbars", f"--user-data-dir={profile}", "--window-size=1024,768",
                   "--virtual-time-budget=2000", "--dump-dom", page.as_uri()]
        returncode, stdout, leftover = run_owned(command)
    finally:
        for _ in range(50):  # the reaped browser may release its profile a moment later
            shutil.rmtree(directory, ignore_errors=True)
            if not os.path.exists(directory):
                break
            time.sleep(0.2)
    output = stdout.decode("utf-8", errors="replace")
    match = re.search(r'<pre id="results">(.*?)</pre>', output, re.S)
    lines = html.unescape(match.group(1)).splitlines() if match else []
    results = [json.loads(line) for line in lines if line.strip()]
    return returncode, results, leftover, os.path.exists(directory)


class BroadcastGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if find_browser() is None:
            if os.environ.get("CI"):
                raise AssertionError("Chrome or Edge is required for the Broadcast geometry check in CI")
            raise unittest.SkipTest("no Chromium browser; set AC6_TEST_BROWSER to run the geometry check")
        sources = {"new": OVERLAY}
        previous = previous_overlay()
        if previous is None and os.environ.get("CI"):
            raise AssertionError(f"overlay.html at {PREVIOUS_VERSION} is not reachable from CI")
        if previous is not None:
            sources["previous"] = previous
        cases = []
        for width, height in SIZES:
            for best in (True, False, None):
                config = dict(CONFIG) if best is None else dict(CONFIG, broadcast_show_best_streak=best)
                cases.append({"name": f"new {width}x{height} {best}", "source": "new", "width": width,
                              "height": height, "config": config, "stats": STATS, "lifetime": LIFETIME,
                              "status": STATUS, "events": WARNINGS})
            cases.append(dict(QUIET, name=f"new-quiet {width}x{height}", source="new", width=width,
                              height=height, lifetime=LIFETIME, status=STATUS))
            if previous is not None and (width, height) in WIDE:
                cases.append({"name": f"previous {width}x{height}", "source": "previous", "width": width,
                              "height": height, "config": dict(CONFIG), "stats": STATS, "lifetime": LIFETIME,
                              "status": STATUS, "events": WARNINGS})
                cases.append(dict(QUIET, name=f"previous-quiet {width}x{height}", source="previous",
                                  width=width, height=height, lifetime=LIFETIME, status=STATUS))
        for width, height in SHORT:
            for best in (True, False):
                cases.append({"name": f"new {width}x{height} {best}", "source": "new", "width": width,
                              "height": height, "config": dict(CONFIG, broadcast_show_best_streak=best),
                              "stats": STATS, "lifetime": LIFETIME, "status": STATUS, "events": WARNINGS})
        cls.expected = len(cases)
        cls.returncode, results, cls.leftover, cls.residue = render(cases, sources)
        cls.results = {row["name"]: row for row in results}
        cls.has_previous = previous is not None

    def case(self, source, width, height, best=None):
        if source in ("previous", "new-quiet", "previous-quiet"):
            name = f"{source} {width}x{height}"
        else:
            name = f"new {width}x{height} {best}"
        self.assertIn(name, self.results, f"{len(self.results)} of {self.expected} layouts reported")
        self.assertNotIn("error", self.results[name])
        return self.results[name]

    def test_the_browser_ran_and_left_nothing_behind(self):
        self.assertEqual(len(self.results), self.expected, "every layout reported")
        self.assertEqual(self.leftover, [], "no browser process left after exit")
        self.assertFalse(self.residue, "the temporary profile and page were removed")

    def test_default_placement_typography_and_transparency(self):
        for width, height in SIZES:
            for best in (True, False, None):
                with self.subTest(size=(width, height), best=best):
                    row = self.case("new", width, height, best)
                    self.assertEqual((row["vw"], row["vh"]), (width, height), "exact Browser Source size")
                    left, top = row["panel"][:2]
                    self.assertAlmostEqual(left, 22, delta=0.5, msg="existing 22 px placement")
                    self.assertAlmostEqual(top, 22, delta=0.5)
                    self.assertEqual(row["font"], ["24px", "800", "24px"], "24 px bold primary text")
                    self.assertEqual(row["background"], ["rgba(0, 0, 0, 0)"] * 2, "transparent page")

    def test_existing_copy_is_rendered(self):
        for best, sub in ((True, "累計　最高連勝 21"), (None, "累計　最高連勝 21"), (False, "")):
            with self.subTest(best=best):
                row = self.case("new", 1920, 1080, best)
                self.assertEqual(row["text"], ["WIN 1234　LOSE 987", "　勝率 55.6%", "　連勝 21",
                                               "　RUSH継続中", sub])

    def test_best_on_and_off(self):
        for width, height in SIZES:
            with self.subTest(size=(width, height)):
                on, off, missing = (self.case("new", width, height, best) for best in (True, False, None))
                self.assertIsNotNone(on["sub"], "ON shows 最高連勝")
                self.assertIsNotNone(missing["sub"], "a missing field shows 最高連勝")
                self.assertEqual(on["panel"], missing["panel"])
                self.assertIsNone(off["sub"], "OFF removes the line")
                sub_height = on["sub"][3] - on["sub"][1]
                removed = (on["panel"][3] - on["panel"][1]) - (off["panel"][3] - off["panel"][1])
                self.assertAlmostEqual(removed, sub_height + 3, delta=1, msg="no blank space is left")
                self.assertAlmostEqual(on["panel"][2], off["panel"][2], delta=1, msg="width is unchanged")

    def test_status_never_animates(self):
        for best in (True, False):
            row = self.case("new", 1920, 1080, best)
            for level, name, running in row["animations"]:
                with self.subTest(best=best, level=level):
                    self.assertEqual(name, "none")
                    self.assertEqual(running, 0, "no animation runs on the page outside a milestone")

    def test_panel_and_warnings_stay_inside_and_never_overlap(self):
        for width, height in SIZES:
            for best in (True, False):
                with self.subTest(size=(width, height), best=best):
                    row = self.case("new", width, height, best)
                    panel = row["panel"]
                    self.assertLessEqual(panel[2], width - 18 + 0.5, "the panel never runs off the page")
                    self.assertLessEqual(panel[3], height)
                    shown = [box for box in row["warnings"] if box]
                    self.assertEqual(len(shown), 2, "detector and config warnings are both shown")
                    self.assert_one_stack(shown)
                    for box in shown:
                        self.assertAlmostEqual(box[2], width - 18, delta=0.5, msg="warnings stay at the right")
                        self.assertGreaterEqual(box[0], 22 - 0.5)
                        self.assertLessEqual(box[3], height)
                        overlaps = box[0] < panel[2] and panel[0] < box[2] and box[1] < panel[3] and panel[1] < box[3]
                        self.assertFalse(overlaps, f"warning {box} covers the panel {panel}")
                    if (width, height) in WIDE:
                        self.assertAlmostEqual(shown[0][1], 18, delta=0.5, msg="beside the panel at 18 px")

    def assert_one_stack(self, shown):
        # Shown warnings stack 3 px apart with no slot kept for a hidden one: the
        # config warning sits right under the detector warning (the previous
        # layout left the stats warning's slot empty between them).
        for above, below in zip(shown, shown[1:]):
            self.assertAlmostEqual(below[1], above[3] + 3, delta=0.5, msg="one stack, 3 px apart")

    def test_a_short_source_keeps_every_warning_on_the_page(self):
        for width, height in SHORT:
            for best in (True, False):
                with self.subTest(size=(width, height), best=best):
                    row = self.case("new", width, height, best)
                    self.assertEqual((row["vw"], row["vh"]), (width, height))
                    self.assertAlmostEqual(row["panel"][0], 22, delta=0.5, msg="existing 22 px placement")
                    self.assertAlmostEqual(row["panel"][1], 22, delta=0.5)
                    self.assertEqual(row["font"], ["24px", "800", "24px"])
                    shown = [box for box in row["warnings"] if box]
                    self.assertEqual(len(shown), 2)
                    self.assertAlmostEqual(shown[0][1], 18, delta=0.5, msg="the previous top-right place")
                    self.assert_one_stack(shown)
                    for box in shown:
                        self.assertAlmostEqual(box[2], width - 18, delta=0.5)
                        self.assertGreaterEqual(box[0], 0)
                        self.assertLessEqual(box[3], height, "no warning is pushed off the page")

    def test_wide_sources_match_the_pre_ui_1b_layout(self):
        if not self.has_previous:
            self.skipTest(f"overlay.html at {PREVIOUS_VERSION} is not in this clone")
        for width, height in WIDE:
            with self.subTest(size=(width, height)):
                before, after = self.case("previous", width, height), self.case("new", width, height, True)
                for index in range(4):
                    self.assertAlmostEqual(before["panel"][index], after["panel"][index], delta=0.5,
                                           msg="the panel is exactly where and as large as it was")
                    self.assertAlmostEqual(before["warnings"][0][index], after["warnings"][0][index], delta=0.5,
                                           msg="the first warning is where it was")
                self.assertEqual(before["text"], after["text"])

    def test_the_quiet_panel_is_unchanged_too(self):
        # No streak status and no warning: the empty status slot and the new
        # wrapping row change nothing on any source size.
        for width, height in SIZES:
            with self.subTest(size=(width, height)):
                quiet = self.case("new-quiet", width, height)
                self.assertEqual(quiet["warnings"], [None, None, None])
                self.assertEqual(quiet["text"], ["WIN 3　LOSE 2", "　勝率 60.0%", "　連勝 1", "", "最高連勝 2"])
                self.assertAlmostEqual(quiet["panel"][0], 22, delta=0.5)
                self.assertAlmostEqual(quiet["panel"][1], 22, delta=0.5)
                if self.has_previous and (width, height) in WIDE:
                    before = self.case("previous-quiet", width, height)
                    for index in range(4):
                        self.assertAlmostEqual(before["panel"][index], quiet["panel"][index], delta=0.5)
                    self.assertEqual(before["text"], quiet["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
