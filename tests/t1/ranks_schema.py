"""Strict canonical schema for the bounded #15-6 self-rank S family."""
import re

from . import schema as s

ADAPTER = "self_rank.v1"
VERSION = "self-rank-s-lobby.v1"
SOURCE = "direct_lobby_rank_panel"
TEMPLATE_ID = "ranks.dev-s-matching"
PANEL_CROP = [600, 432, 742, 600]
HEADER_CROP = [80, 40, 560, 100]
POSITIVE_SCREENS = ("lobby_idle", "lobby_matching")
NEGATIVE_SCREENS = ("lobby_dimmed_start", "lobby_crossfade", "lobby_truncated_crop", "system_menu",
                    "garage_license", "match_intro", "custom_room", "custom_result", "rank_mode_select")
_IMAGE_KEYS = ("kind", "path", "format", "width", "height", "bytes", "sha256")


def _image(source, image, category, role):
    s._object(source, image, _IMAGE_KEYS)
    s._enum(source, image["kind"], ("roi",))
    s._enum(source, image["format"], ("png",))
    if (not isinstance(image["path"], str) or not image["path"].startswith(f"ranks/{category}/")
            or not image["path"].endswith(f"-{role}.png")):
        s._fail(source, f"{role} PNG must live in its category and end with -{role}.png")
    s._int(source, image["bytes"], 1, 1024 * 1024)
    if not isinstance(image["sha256"], str) or not s.SHA256_PATTERN.fullmatch(image["sha256"]):
        s._fail(source, "invalid image SHA-256")


def validate(source, record, category, known_tags):
    s._object(source, record, ("schema_version", "id", "family", "category", "input", "context", "truth",
                              "checks", "coverage", "provenance", "review"))
    s._int(source + ".schema_version", record["schema_version"], 1, 1)
    s._identifier(source + ".id", record["id"])
    if not record["id"].startswith("ranks."):
        s._fail(source, "rank records are named ranks.*")
    s._enum(source + ".family", record["family"], ("ranks",))
    s._enum(source + ".category", category, ("s_rank", "negatives"))
    if record["category"] != category:
        s._fail(source, "category does not match its directory")
    positive = category == "s_rank"
    panel, header = record["input"], record["context"]
    _image(source + ".input", panel, category, "panel")
    _image(source + ".context", header, category, "header")
    if panel["path"][:-len("-panel.png")] != header["path"][:-len("-header.png")]:
        s._fail(source, "panel and header crops must be named as one pair")
    s._int(source, panel["width"], 1, PANEL_CROP[2] - PANEL_CROP[0])
    s._int(source, panel["height"], 1, PANEL_CROP[3] - PANEL_CROP[1])
    if (header["width"], header["height"]) != (480, 60):
        s._fail(source, "the context crop is the complete native header ROI")

    truth = s._object(source + ".truth", record["truth"], ("self_rank", "screen", "visible_text", "self_identity"))
    s._enum(source + ".truth.self_rank", truth["self_rank"], ("S",) if positive else ("unknown",))
    s._enum(source + ".truth.screen", truth["screen"], POSITIVE_SCREENS if positive else NEGATIVE_SCREENS)
    s._text(source + ".truth.visible_text", truth["visible_text"])
    s._text(source + ".truth.self_identity", truth["self_identity"])
    if positive and (panel["width"], panel["height"]) != (PANEL_CROP[2] - PANEL_CROP[0], PANEL_CROP[3] - PANEL_CROP[1]):
        s._fail(source, "a positive requires the complete native RANK panel crop")

    checks = s._object(source + ".checks", record["checks"], ("adapter", "self_rank", "status", "version", "source"))
    for key, expected in (("adapter", ADAPTER), ("self_rank", "S" if positive else None),
                          ("status", "recognized" if positive else "failed"),
                          ("version", VERSION), ("source", SOURCE)):
        if checks[key] != expected or type(checks[key]) is not type(expected):
            s._fail(source + ".checks." + key, f"must be {expected!r}")
    s.validate_coverage_tags(source, record["coverage"], known_tags)

    prov = s._object(source + ".provenance", record["provenance"],
                     ("source_type", "video_id", "reference", "timestamp_ms", "source_dimensions", "crop_xyxy",
                      "context_crop_xyxy", "source_frame_sha256", "split", "synthetic", "template_source"))
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
            or crop[:2] != PANEL_CROP[:2] or crop[2:] != [crop[0] + panel["width"], crop[1] + panel["height"]]):
        s._fail(source, "panel crop does not match the native RANK panel geometry")
    if prov["context_crop_xyxy"] != HEADER_CROP or any(type(x) is not int for x in prov["context_crop_xyxy"]):
        s._fail(source, "context crop does not match the native header geometry")
    if not isinstance(prov["source_frame_sha256"], str) or not s.SHA256_PATTERN.fullmatch(prov["source_frame_sha256"]):
        s._fail(source, "invalid original-frame SHA-256")
    s._enum(source, prov["split"], ("dev", "validation"))
    if prov["synthetic"] is not False:
        s._fail(source, "canonical rank evidence must be genuine")
    s._bool(source, prov["template_source"])
    if prov["template_source"] and (prov["split"] != "dev" or not positive or record["id"] != TEMPLATE_ID):
        s._fail(source, "only the pinned dev positive is the template source")

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
