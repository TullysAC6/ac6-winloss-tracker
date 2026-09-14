"""T1 reports: stdout summary, JSON and JUnit XML.

Reports never carry an absolute user path: the repository, the T1 root, TEMP
and the home directory are replaced with placeholders before anything is
written or printed.
"""
from __future__ import annotations

import json
import os
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path


def placeholders(repo_root, suite_root=None):
    pairs = [(repo_root, "<repo>")]
    if suite_root is not None:
        pairs.append((suite_root, "<t1-root>"))
    pairs += [(tempfile.gettempdir(), "<temp>"), (Path.home(), "<home>")]
    expanded = []
    for path, token in pairs:
        text = os.path.realpath(os.fspath(path))
        for variant in {text, text.replace("\\", "/"), os.fspath(path), os.fspath(path).replace("\\", "/")}:
            if variant:
                expanded.append((variant, token))
    return sorted(expanded, key=lambda pair: len(pair[0]), reverse=True)


def sanitize(value, replacements):
    if isinstance(value, str):
        for original, token in replacements:
            if original in value:
                value = value.replace(original, token)
            lowered = original.lower()
            if lowered != original and lowered in value.lower():
                start = value.lower().find(lowered)
                while start != -1:
                    value = value[:start] + token + value[start + len(original):]
                    start = value.lower().find(lowered, start + len(token))
        return value
    if isinstance(value, dict):
        return {key: sanitize(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item, replacements) for item in value]
    return value


def summary_lines(report):
    summary = report["summary"]
    lines = [
        "=" * 70,
        f"T1 status: {report['status']}"
        + ("" if report["canonical_corpus"] else "  (non-canonical corpus: not a T1 gate result)"),
        f"total={summary['total']} passed={summary['passed']} failed={summary['failed']} "
        f"skipped={summary['skipped']} duration={summary['duration_s']:.2f}s",
    ]
    corpus = report.get("corpus")
    if corpus:
        lines.append(f"corpus: {corpus['images']} images ({corpus['image_bytes']} bytes), "
                     f"{corpus['sequences']} sequences, sha256={corpus['sha256'][:16]}")
    for problem in report["infrastructure_failures"]:
        lines.append(f"INFRASTRUCTURE FAILURE [{problem['stage']}]: {problem['reason']}")
    missing = report.get("coverage", {}).get("missing") or []
    if missing:
        lines.append(f"REQUIRED COVERAGE NOT PROVEN: {', '.join(missing)}")
    for case in report["cases"]:
        if case["status"] == "PASS":
            continue
        for item in case["failures"][:20]:
            lines.append(
                f"FAIL {case['id']} step={item.get('step')} stage={item['stage']} check={item['check']} "
                f"expected={json.dumps(item['expected'], ensure_ascii=False)} "
                f"actual={json.dumps(item['actual'], ensure_ascii=False)} reason={item['reason']}")
    performance = report.get("performance")
    if performance and performance.get("slowest"):
        slow = ", ".join(f"{item['id']}={item['duration_s']:.2f}s" for item in performance["slowest"])
        lines.append(f"slowest: {slow}")
    lines.append("=" * 70)
    return lines


def write_json(path, report):
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_junit(path, report):
    summary = report["summary"]
    suites = ElementTree.Element("testsuites", name="T1 fixture replay", tests=str(summary["total"]),
                                 failures=str(summary["failed"]), errors="0", skipped="0",
                                 time=f"{summary['duration_s']:.3f}")
    suite = ElementTree.SubElement(suites, "testsuite", name="t1", tests=str(summary["total"]),
                                   failures=str(summary["failed"]), errors="0", skipped="0",
                                   time=f"{summary['duration_s']:.3f}")
    if report["infrastructure_failures"]:
        case = ElementTree.SubElement(suite, "testcase", classname="t1.infrastructure", name="corpus-and-cleanup",
                                      time="0")
        for problem in report["infrastructure_failures"]:
            node = ElementTree.SubElement(case, "failure", message=f"[{problem['stage']}] {problem['reason']}")
            node.text = json.dumps(problem, ensure_ascii=False)
    for item in report["cases"]:
        case = ElementTree.SubElement(suite, "testcase", classname=f"t1.{item['kind']}", name=item["id"],
                                      time=f"{item['duration_s']:.3f}")
        if item["status"] != "PASS":
            first = item["failures"][0] if item["failures"] else {"stage": "unknown", "reason": "failed"}
            node = ElementTree.SubElement(case, "failure", message=f"[{first['stage']}] {first['reason']}")
            node.text = json.dumps(item["failures"], ensure_ascii=False, indent=1)
    ElementTree.ElementTree(suites).write(path, encoding="utf-8", xml_declaration=True)
