"""The T1 runner: discovery, validation, bounded replay, comparison, cleanup, reports.

Only this parent starts workers. Each worker gets its own owned root, its own
kill-on-close job and a real-time deadline; after it exits the job must be empty
and the root must be removable, or the case fails. Nothing is ever skipped: a
case that cannot run, a missing requirement or an infrastructure problem makes
the whole run FAIL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import compare, report as reporting
from .corpus import CorpusError, load_corpus
from .process import WorkerJob
from .strict_json import MetadataError, loads_strict

DEFAULT_CASE_TIMEOUT = 10.0
DEFAULT_SUITE_TIMEOUT = 120.0
JOB_EXIT_GRACE_SECONDS = 5.0
REPLAYED_SOURCES = ("result_detector.py", "result_gate.py", "game_capture.py", "owned_worker.py",
                    "detector_templates.json")
EXPECTED_PROBES = frozenset(("socket", "dns", "process", "write_outside_root", "denied_import", "native_window_dll"))
WORKER_SCRIPT = Path(__file__).resolve().with_name("worker.py")
CREATE_NO_WINDOW = 0x08000000


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_git_head(repo_root):
    """The checked-out commit, read from files (no git process)."""
    try:
        dot_git = Path(repo_root) / ".git"
        if dot_git.is_file():
            line = dot_git.read_text(encoding="utf-8").strip()
            if not line.startswith("gitdir:"):
                return None
            git_dir = (Path(repo_root) / line[7:].strip()).resolve()
        else:
            git_dir = dot_git
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{40}", head):
            return head
        if not head.startswith("ref: "):
            return None
        ref = head[5:].strip()
        common = git_dir
        common_file = git_dir / "commondir"
        if common_file.is_file():
            common = (git_dir / common_file.read_text(encoding="utf-8").strip()).resolve()
        for base in (git_dir, common):
            candidate = base / ref
            if candidate.is_file():
                value = candidate.read_text(encoding="utf-8").strip()
                return value if re.fullmatch(r"[0-9a-f]{40}", value) else None
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref and re.fullmatch(r"[0-9a-f]{40}", parts[0]):
                    return parts[0]
    except OSError:
        return None
    return None


def _environment(repo_root):
    try:
        from importlib.metadata import version
        mss_version = version("mss")
    except Exception:
        mss_version = None
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "mss": mss_version,
        "github_sha": os.environ.get("GITHUB_SHA"),
        "git_head": read_git_head(repo_root),
    }


def _remove_tree(path, attempts=10):
    for attempt in range(attempts):
        try:
            if path.exists():
                shutil.rmtree(path)
            return not path.exists()
        except OSError:
            time.sleep(0.2 * (attempt + 1))
    return not path.exists()


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)[:80]


def _live_data_roots():
    roots = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(os.path.join(local, "AC6WinLossTracker"))
    return roots


def _spec(case, corpus, repo_root, fixtures_root, forbidden_roots):
    if case.kind == "image":
        images = {case.id: case.record["input"]}
        data = None
    else:
        data = case.record["input"]
        referenced = set()
        for step in data["steps"]:
            if "frame" in step:
                referenced.add(step["frame"])
            if "wgc" in step and step["wgc"]["sample"] is not None:
                referenced.add(step["wgc"]["sample"]["frame"])
        images = {image_id: corpus.images[image_id]["input"] for image_id in sorted(referenced)}
    return {"case_id": case.id, "kind": case.kind, "repo_root": str(repo_root), "fixtures_root": str(fixtures_root),
            "forbidden_roots": list(forbidden_roots), "images": images, "input": data}


def _validate_worker_result(result, case, source_hashes):
    problems = []
    if result.get("worker_version") != 1:
        problems.append("unknown worker result version")
    if result.get("status") != "completed":
        problems.append(f"worker did not complete (stage {result.get('stage')}): {result.get('error')}")
        return problems
    if result.get("case_id") != case.id or result.get("kind") != case.kind:
        problems.append("worker reported a different case")
    isolation = result.get("isolation") or {}
    if isolation.get("modules_before_isolation") != []:
        problems.append("production modules were imported before isolation")
    for name in ("LOCALAPPDATA", "TEMP", "TMP", "cwd", "tempfile"):
        if isolation.get(name) != "owned":
            problems.append(f"{name} was not proved to be inside the owned root")
    probes = isolation.get("guard_probes") or {}
    if set(probes) != EXPECTED_PROBES or any(value is not True for value in probes.values()):
        problems.append(f"guard probes did not all hold: {probes}")
    if isolation.get("denied_modules_loaded") != []:
        problems.append("denied modules were loaded during replay")
    if result.get("source_sha256") != source_hashes:
        problems.append("the worker replayed different production sources than the runner hashed")
    if "actual" not in result:
        problems.append("worker reported no actual output")
    return problems


def run_case(case, corpus, repo_root, fixtures_root, suite_root, index, timeout, python, worker_script,
             source_hashes, forbidden_roots):
    started = time.monotonic()
    case_root = suite_root / f"{index:03d}-{_safe_name(case.id)}"
    entry = {"id": case.id, "kind": case.kind, "source": case.source, "status": "FAIL", "failures": [],
             "coverage": list(case.record["coverage"]), "duration_s": 0.0}
    job = None
    process = None
    try:
        for name in ("localappdata", "temp"):
            (case_root / name).mkdir(parents=True)
        spec_path = case_root / "spec.json"
        spec_path.write_text(json.dumps(_spec(case, corpus, repo_root, fixtures_root, forbidden_roots)),
                             encoding="utf-8")
        environment = dict(os.environ)
        environment.update(LOCALAPPDATA=str(case_root / "localappdata"), TEMP=str(case_root / "temp"),
                           TMP=str(case_root / "temp"), AC6_T1_CASE_ROOT=str(case_root))
        log_path = case_root / "worker.log"
        job = WorkerJob()
        with open(log_path, "wb") as log:
            process = subprocess.Popen(
                [python, "-B", "-E", "-X", "utf8", str(worker_script), str(spec_path)],
                cwd=str(case_root), env=environment, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                job.assign(process.pid)
            except OSError as error:
                process.kill()
                process.wait(10)
                raise RuntimeError(f"worker could not be placed in its job: {error}") from None
            entry["worker_pid"] = process.pid
            try:
                process.wait(timeout=max(0.1, timeout))
            except subprocess.TimeoutExpired:
                entry["failures"].append(compare.failure("timeout", "case_timeout", f"<= {timeout:.1f}s",
                                                         "still running", "the worker exceeded its real-time deadline"))
                job.terminate()
                process.wait(10)
        entry["exit_code"] = process.returncode
        # Job accounting can trail the worker's exit by a moment, so a leak is
        # only declared when something is still in the job after a bounded grace.
        leak_failure = None
        leaked = job.wait_empty(JOB_EXIT_GRACE_SECONDS)
        if leaked:
            leak_failure = compare.failure("cleanup", "active_processes", 0,
                                           {"count": leaked, "members": job.members()},
                                           "a worker child or grandchild outlived the worker")
            job.terminate()
            job.wait_empty(10.0)
        entry["peak_process_memory_bytes"] = job.peak_process_memory()
        entry["job_total_processes"] = job.total_processes()
        result_path = case_root / "result.json"
        result = None
        if result_path.is_file():
            try:
                result = loads_strict(result_path.read_bytes(), "worker result")
            except MetadataError as error:
                entry["failures"].append(compare.failure("worker", "result", "valid JSON", None, str(error)))
        if process.returncode != 0 and not any(item["stage"] == "timeout" for item in entry["failures"]):
            reason = (result or {}).get("error") or "worker exited abnormally"
            entry["failures"].append(compare.failure("worker", "exit_code", 0, process.returncode, reason))
        if result is None and not entry["failures"]:
            entry["failures"].append(compare.failure("worker", "result", "result.json", None,
                                                     "the worker wrote no result"))
        if result is not None and process.returncode == 0:
            for problem in _validate_worker_result(result, case, source_hashes):
                entry["failures"].append(compare.failure("isolation", "worker_result", "valid", "invalid", problem))
            if not entry["failures"]:
                actual = result["actual"]
                if case.kind == "image":
                    entry["failures"].extend(compare.compare_image(case.record, actual))
                    entry["actual"] = {"frame_class": actual.get("frame_class")}
                else:
                    entry["failures"].extend(compare.compare_sequence(case.record, actual))
                    entry["actual"] = {"totals": compare.derive_totals(actual.get("observations") or [])}
                entry["timings"] = result.get("timings")
                entry["peak_working_set_bytes"] = result.get("peak_working_set_bytes")
        # Recorded after the comparison so a leak never hides a replay result.
        if leak_failure is not None:
            entry["failures"].append(leak_failure)
        if entry["failures"] and log_path.is_file():
            entry["worker_log_tail"] = log_path.read_bytes()[-4000:].decode("utf-8", "replace")
    except Exception as error:
        entry["failures"].append(compare.failure("runner", "exception", None, type(error).__name__, str(error)))
        if process is not None and process.poll() is None:
            try:
                job.terminate()
            except Exception:
                process.kill()
            process.wait(10)
    finally:
        if job is not None:
            try:
                if job.active_processes():
                    job.terminate()
            except OSError:
                pass
            job.close()
        if case_root.exists() and not _remove_tree(case_root):
            entry["failures"].append(compare.failure("cleanup", "case_root", "removed", "residue",
                                                     "the case root could not be removed"))
    entry["duration_s"] = round(time.monotonic() - started, 4)
    entry["status"] = "PASS" if not entry["failures"] else "FAIL"
    return entry


def run_t1(repo_root, report_dir, case_timeout=DEFAULT_CASE_TIMEOUT, suite_timeout=DEFAULT_SUITE_TIMEOUT,
           python=sys.executable, fixtures_root=None, worker_script=WORKER_SCRIPT, stream=None,
           forbidden_roots=None):
    stream = stream or sys.stdout
    started = time.monotonic()
    repo_root = Path(repo_root).resolve()
    canonical_root = repo_root / "tests" / "fixtures"
    fixtures_root = Path(fixtures_root).resolve() if fixtures_root is not None else canonical_root
    report_dir = Path(report_dir)
    forbidden_roots = _live_data_roots() if forbidden_roots is None else list(forbidden_roots)
    result = {"t1_report_version": 1, "status": "FAIL", "canonical_corpus": fixtures_root == canonical_root,
              "environment": _environment(repo_root), "infrastructure_failures": [], "cases": [],
              "limits": {"case_timeout_s": case_timeout, "suite_timeout_s": suite_timeout}}
    suite_root = None
    try:
        result["code"] = {"sources_sha256": {name: _sha256(repo_root / name) for name in REPLAYED_SOURCES}}
        result["templates"] = {"detector_templates.json": result["code"]["sources_sha256"]["detector_templates.json"]}
        try:
            corpus = load_corpus(fixtures_root)
        except CorpusError as error:
            result["infrastructure_failures"].append({"stage": "corpus", "reason": str(error)})
            corpus = None
        if corpus is not None:
            result["corpus"] = corpus.summary()
            suite_root = Path(tempfile.mkdtemp(prefix="ac6-t1-suite-"))
            for index, case in enumerate(corpus.cases):
                remaining = suite_timeout - (time.monotonic() - started)
                if remaining <= 0:
                    result["cases"].append({"id": case.id, "kind": case.kind, "source": case.source,
                                            "status": "FAIL", "coverage": list(case.record["coverage"]),
                                            "duration_s": 0.0, "failures": [compare.failure(
                                                "timeout", "suite_timeout", f"<= {suite_timeout:.1f}s",
                                                "exhausted", "the suite deadline passed before this case ran")]})
                    continue
                entry = run_case(case, corpus, repo_root, fixtures_root, suite_root, index,
                                 min(case_timeout, remaining), python, Path(worker_script),
                                 result["code"]["sources_sha256"], forbidden_roots)
                result["cases"].append(entry)
                mark = "ok  " if entry["status"] == "PASS" else "FAIL"
                print(f"  [{mark}] {case.id} ({entry['duration_s']:.2f}s)", file=stream, flush=True)
            passed_tags = {tag for entry in result["cases"] if entry["status"] == "PASS" for tag in entry["coverage"]}
            missing = sorted(set(corpus.required_tags) - passed_tags)
            result["coverage"] = {
                "required": sorted(corpus.required_tags),
                "proven_by": {tag: sorted(entry["id"] for entry in result["cases"]
                                          if entry["status"] == "PASS" and tag in entry["coverage"])
                              for tag in sorted(corpus.required_tags)},
                "missing": missing,
            }
            if missing:
                result["infrastructure_failures"].append(
                    {"stage": "coverage", "reason": f"required coverage not proven by a passing case: {missing}"})
    except Exception as error:
        result["infrastructure_failures"].append({"stage": "runner", "reason": f"{type(error).__name__}: {error}"})
    finally:
        if suite_root is not None and not _remove_tree(suite_root):
            result["infrastructure_failures"].append({"stage": "cleanup", "reason": "T1 suite root could not be removed"})
        if suite_root is not None and suite_root.exists():
            result["infrastructure_failures"].append({"stage": "cleanup", "reason": "T1 temp residue remains"})

    cases = result["cases"]
    failed = sum(1 for entry in cases if entry["status"] != "PASS")
    duration = time.monotonic() - started
    result["summary"] = {"total": len(cases), "passed": len(cases) - failed, "failed": failed, "skipped": 0,
                         "duration_s": round(duration, 3)}
    ok = bool(cases) and not failed and not result["infrastructure_failures"] and result["canonical_corpus"]
    if not cases and not result["infrastructure_failures"]:
        result["infrastructure_failures"].append({"stage": "corpus", "reason": "no T1 case ran"})
    result["status"] = "PASS" if ok and not result["infrastructure_failures"] else "FAIL"
    if not result["canonical_corpus"] and not failed and not result["infrastructure_failures"] and cases:
        result["status"] = "PASS-NONCANONICAL"
    durations = sorted(cases, key=lambda entry: entry["duration_s"], reverse=True)
    result["performance"] = {
        "cases": len(cases),
        "total_duration_s": round(duration, 3),
        "slowest": [{"id": entry["id"], "duration_s": entry["duration_s"]} for entry in durations[:5]],
        "max_peak_process_memory_bytes": max((entry.get("peak_process_memory_bytes") or 0 for entry in cases),
                                             default=0),
        "max_peak_working_set_bytes": max((entry.get("peak_working_set_bytes") or 0 for entry in cases), default=0),
    }
    replacements = reporting.placeholders(repo_root, suite_root)
    safe = reporting.sanitize(result, replacements)
    report_dir.mkdir(parents=True, exist_ok=True)
    reporting.write_json(report_dir / "t1-report.json", safe)
    reporting.write_junit(report_dir / "t1-junit.xml", safe)
    for line in reporting.summary_lines(safe):
        print(line, file=stream, flush=True)
    print(f"T1 reports: {reporting.sanitize(str(report_dir), replacements)}", file=stream, flush=True)
    return 0 if result["status"] == "PASS" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="T1 fixture replay (issue #14)")
    parser.add_argument("--report-dir", default=os.path.join(tempfile.gettempdir(), "ac6-t1-report"))
    parser.add_argument("--case-timeout", type=float, default=DEFAULT_CASE_TIMEOUT)
    parser.add_argument("--suite-timeout", type=float, default=DEFAULT_SUITE_TIMEOUT)
    parser.add_argument("--fixtures", default=None,
                        help="alternate corpus for harness self-tests; never reported as a T1 PASS")
    arguments = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]
    return run_t1(repo_root, arguments.report_dir, arguments.case_timeout, arguments.suite_timeout,
                  fixtures_root=arguments.fixtures)
