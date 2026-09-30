"""Strict canonical schema for the bounded #15-4 header family."""
import re

from . import schema as s

ADAPTER = "match_header.v1"
VERSION = "rank-single-header.v1"


def validate(source, record, category, known_tags):
    s._object(source, record, ("schema_version", "id", "family", "category", "input", "truth",
                              "checks", "coverage", "provenance", "review"))
    s._int(source + ".schema_version", record["schema_version"], 1, 1)
    s._identifier(source + ".id", record["id"])
    if not record["id"].startswith("metadata."):
        s._fail(source, "metadata records are named metadata.*")
    s._enum(source + ".family", record["family"], ("match_metadata",))
    s._enum(source + ".category", category, ("ranked_single", "negatives"))
    if record["category"] != category:
        s._fail(source, "category does not match its directory")
    image = s._object(source + ".input", record["input"],
                      ("kind", "path", "format", "width", "height", "bytes", "sha256"))
    s._enum(source, image["kind"], ("roi",))
    s._enum(source, image["format"], ("png",))
    if not isinstance(image["path"], str) or not image["path"].startswith(f"match_metadata/{category}/") or not image["path"].endswith(".png"):
        s._fail(source, "metadata PNG must live in its category")
    s._int(source, image["width"], 1, 480)
    s._int(source, image["height"], 1, 60)
    s._int(source, image["bytes"], 1, 1024 * 1024)
    if not isinstance(image["sha256"], str) or not s.SHA256_PATTERN.fullmatch(image["sha256"]):
        s._fail(source, "invalid image SHA-256")
    truth = s._object(source + ".truth", record["truth"], ("match_type", "match_format", "screen", "visible_text"))
    positive = category == "ranked_single"
    for key, expected in (("match_type", "ranked" if positive else "unknown"),
                          ("match_format", "single" if positive else "unknown")):
        s._enum(source + ".truth." + key, truth[key], (expected,))
    s._enum(source, truth["screen"], ("lobby",) if positive else ("assembly", "sortie", "combat", "garage", "loading", "partial_header"))
    s._text(source, truth["visible_text"])
    if positive and (truth["visible_text"] != "RANK MATCH: SINGLE" or (image["width"], image["height"]) != (480, 60)):
        s._fail(source, "positive requires the complete direct header")
    checks = s._object(source + ".checks", record["checks"],
                       ("adapter", "match_type", "match_format", "status", "version", "source"))
    for key, expected in (("adapter", ADAPTER), ("match_type", truth["match_type"]),
                          ("match_format", truth["match_format"]), ("status", "recognized" if positive else "failed"),
                          ("version", VERSION), ("source", "direct_header")):
        s._enum(source + ".checks." + key, checks[key], (expected,))
    s.validate_coverage_tags(source, record["coverage"], known_tags)
    prov = s._object(source + ".provenance", record["provenance"],
                     ("source_type", "video_id", "reference", "timestamp_ms", "source_dimensions", "crop_xyxy",
                      "source_frame_sha256", "split", "synthetic", "template_source"))
    s._enum(source, prov["source_type"], ("original_video_frame",))
    if not isinstance(prov["video_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", prov["video_id"]):
        s._fail(source, "invalid video identity")
    if prov["reference"] != "https://www.youtube.com/watch?v=" + prov["video_id"]:
        s._fail(source, "reference differs from source identity")
    s._int(source, prov["timestamp_ms"], 0, 24 * 3600 * 1000)
    if prov["source_dimensions"] != [1920, 1080] or any(type(x) is not int for x in prov["source_dimensions"]):
        s._fail(source, "source layout must be native 1920x1080")
    crop = prov["crop_xyxy"]
    if (not isinstance(crop, list) or len(crop) != 4 or any(type(x) is not int for x in crop)
            or crop[:2] != [80, 40] or crop[2:] != [80 + image["width"], 40 + image["height"]]):
        s._fail(source, "crop does not match native header geometry")
    if not isinstance(prov["source_frame_sha256"], str) or not s.SHA256_PATTERN.fullmatch(prov["source_frame_sha256"]):
        s._fail(source, "invalid original-frame SHA-256")
    s._enum(source, prov["split"], ("dev", "validation"))
    if prov["synthetic"] is not False:
        s._fail(source, "canonical metadata evidence must be genuine")
    s._bool(source, prov["template_source"])
    if prov["template_source"] and (prov["split"] != "dev" or not positive):
        s._fail(source, "template evidence must be dev positive")
    review = s._object(source + ".review", record["review"], ("method", "verifier", "date", "basis", "privacy", "corrections"))
    s._enum(source, review["method"], ("original_frame_and_crop_visual_inspection",))
    for key in ("verifier", "date", "basis", "privacy"):
        s._text(source, review[key])
    corrections = review["corrections"]
    if not isinstance(corrections, list) or len(corrections) > 4:
        s._fail(source, "corrections must be a bounded list")
    for correction in corrections:
        s._object(source, correction, ("previous_truth", "reason", "verifier", "date", "evidence_sha256"))
        s._object(source, correction["previous_truth"], tuple(truth))
        for value in correction["previous_truth"].values():
            s._text(source, value)
        for key in ("reason", "verifier", "date"):
            s._text(source, correction[key])
        if not isinstance(correction["evidence_sha256"], str) or not s.SHA256_PATTERN.fullmatch(correction["evidence_sha256"]):
            s._fail(source, "correction needs source evidence SHA-256")
    return record
