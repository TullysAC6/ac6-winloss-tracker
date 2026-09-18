"""T1 replay worker: one case, one process.

Started only by the T1 runner, as ``python -B -E -X utf8 tests/t1/worker.py
<spec.json>``, with LOCALAPPDATA, TEMP, TMP and the working directory already
pointing into a fresh owned root. Before any production import the worker proves
that isolation, installs the guards and probes them. Only then does it import
result_detector, result_gate and game_capture and replay the case. It reports
what production produced; it is never told what was expected.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPLAYED_MODULES = ("result_detector", "result_gate", "game_capture", "owned_worker")
WORKER_VERSION = 1


def _drop_script_directory():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [entry for entry in sys.path if os.path.abspath(entry or ".") != here]


def prove_isolation(case_root):
    from t1.guards import DENIED_IMPORTS
    from t1.paths import is_within

    early = sorted(name for name in set(REPLAYED_MODULES) | set(DENIED_IMPORTS) if name in sys.modules)
    if early:
        raise RuntimeError(f"production modules were imported before isolation: {early}")
    if not sys.flags.dont_write_bytecode or not sys.flags.ignore_environment:
        raise RuntimeError("the worker must run with -B and -E")
    proof = {"modules_before_isolation": early, "flags": {"B": True, "E": True}}
    for name in ("LOCALAPPDATA", "TEMP", "TMP"):
        value = os.environ.get(name)
        if not value or not is_within(case_root, value):
            raise RuntimeError(f"{name} is not inside the owned case root")
        proof[name] = "owned"
    if not is_within(case_root, os.getcwd()):
        raise RuntimeError("the working directory is not inside the owned case root")
    import tempfile
    if not is_within(case_root, tempfile.gettempdir()):
        raise RuntimeError("tempfile does not resolve inside the owned case root")
    proof["cwd"] = "owned"
    proof["tempfile"] = "owned"
    return proof


def _peak_working_set():
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    if not kernel.K32GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return int(counters.PeakWorkingSetSize)


def _write_result(path, payload):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def main(argv):
    started = time.perf_counter()
    _drop_script_directory()
    spec_path = Path(argv[1]).resolve()
    case_root = Path(os.environ["AC6_T1_CASE_ROOT"]).resolve(strict=True)
    tests_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(tests_root))
    result_path = case_root / "result.json"
    payload = {"worker_version": WORKER_VERSION, "status": "error", "stage": "isolation"}
    try:
        isolation = prove_isolation(case_root)
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        payload.update(case_id=spec["case_id"], kind=spec["kind"])
        repo_root = Path(spec["repo_root"]).resolve(strict=True)
        fixtures_root = Path(spec["fixtures_root"]).resolve(strict=True)

        from t1 import guards
        payload["stage"] = "guards"
        guards.install(case_root, forbidden_roots=spec["forbidden_roots"])
        isolation["guard_probes"] = guards.probe(case_root, repo_root / "t1-guard-probe.txt")
        if any(value is not True for value in isolation["guard_probes"].values()):
            raise RuntimeError(f"a T1 guard did not hold: {isolation['guard_probes']}")

        payload["stage"] = "decode"
        from t1.replay import classify_image, load_verified_image, run_sequence
        decode_started = time.perf_counter()
        images = {image_id: load_verified_image(fixtures_root, image_input)
                  for image_id, image_input in sorted(spec["images"].items())}
        decode_seconds = time.perf_counter() - decode_started

        payload["stage"] = "import"
        import_started = time.perf_counter()
        sys.path.insert(0, str(repo_root))
        import game_capture
        import result_detector
        import result_gate
        from mss.screenshot import ScreenShot
        import_seconds = time.perf_counter() - import_started
        templates_path = repo_root / "detector_templates.json"
        modules = {"result_detector": result_detector, "result_gate": result_gate, "game_capture": game_capture,
                   "game_capture_type": game_capture.GameCapture, "screenshot_type": ScreenShot}
        source_hashes = {name: hashlib.sha256((repo_root / name).read_bytes()).hexdigest()
                         for name in ("result_detector.py", "result_gate.py", "game_capture.py",
                                      "owned_worker.py", "detector_templates.json")}
        for name in REPLAYED_MODULES:
            module_file = Path(sys.modules[name].__file__).resolve()
            if module_file.parent != repo_root:
                raise RuntimeError(f"{name} was imported from outside the repository under test")

        payload["stage"] = "replay"
        replay_started = time.perf_counter()
        if spec["kind"] == "image":
            actual = classify_image(result_detector, templates_path, images[spec["case_id"]])
        elif spec["kind"] == "sequence":
            actual = run_sequence(modules, spec["input"], images, templates_path, case_root)
        else:
            raise RuntimeError(f"unknown case kind {spec['kind']!r}")
        replay_seconds = time.perf_counter() - replay_started

        forbidden_loaded = sorted(name for name in guards.DENIED_IMPORTS if name in sys.modules)
        if forbidden_loaded:
            raise RuntimeError(f"denied modules were loaded during replay: {forbidden_loaded}")
        isolation["denied_modules_loaded"] = forbidden_loaded
        payload.update(
            status="completed",
            stage="report",
            isolation=isolation,
            actual=actual,
            source_sha256=source_hashes,
            timings={"decode_s": round(decode_seconds, 4), "import_s": round(import_seconds, 4),
                     "replay_s": round(replay_seconds, 4),
                     "worker_s": round(time.perf_counter() - started, 4)},
            peak_working_set_bytes=_peak_working_set(),
            python={"version": sys.version.split()[0], "implementation": sys.implementation.name},
        )
        _write_result(result_path, payload)
        return 0
    except BaseException as error:  # reported, then the non-zero exit fails the case
        payload["error"] = f"{type(error).__name__}: {error}"
        try:
            _write_result(result_path, payload)
        except Exception:
            pass
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
