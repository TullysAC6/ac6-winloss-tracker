"""T1 corpus discovery and integrity validation (parent side, no production import).

Everything that can be proved without running production is proved here, and any
doubt fails the whole run: an empty corpus, an unknown family, a record in a
reserved family, a duplicate id or key, an orphaned or shared image, a hash,
size or dimension mismatch, an undecodable image or an uncovered requirement.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from . import schema
from .images import ImageError, decode_image
from .paths import FixturePathError, resolve_fixture_file
from .strict_json import MetadataError, loads_strict

FAMILIES_FILE = "families.json"
COVERAGE_FILE = "required-coverage.json"
LEGACY_FILE = "legacy-coverage.json"
REGISTRY_FILES = frozenset((FAMILIES_FILE, COVERAGE_FILE, LEGACY_FILE))
ALLOWED_SUFFIXES = frozenset((".json", ".ppm", ".png", ".md"))
IMAGE_SUFFIXES = frozenset((".ppm", ".png"))
MAX_RECORDS = 1000
MAX_CORPUS_IMAGE_BYTES = 64 * 1024 * 1024
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class CorpusError(ValueError):
    """The corpus cannot be trusted; T1 fails before any replay."""


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    source: str
    record: dict
    metadata_sha256: str


@dataclass
class Corpus:
    root: Path
    families: dict
    required_tags: dict
    images: dict = field(default_factory=dict)
    sequences: dict = field(default_factory=dict)
    cases: list = field(default_factory=list)
    files: dict = field(default_factory=dict)
    image_bytes: int = 0

    def summary(self):
        entries = sorted(self.files.items())
        digest = hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode("utf-8")).hexdigest()
        return {
            "records": len(self.cases),
            "images": len(self.images),
            "sequences": len(self.sequences),
            "image_bytes": self.image_bytes,
            "files": len(entries),
            "sha256": digest,
            "families": {name: family["status"] for name, family in sorted(self.families.items())},
        }


def _read_json(path, relative):
    try:
        data = path.read_bytes()
    except OSError as error:
        raise CorpusError(f"{relative}: cannot read: {error}") from None
    try:
        return loads_strict(data, relative), hashlib.sha256(data).hexdigest()
    except MetadataError as error:
        raise CorpusError(str(error)) from None


def _is_link(path):
    info = os.lstat(path)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def _walk(root):
    """Yield (relative posix path, absolute path) for every file, refusing links."""
    stack = [Path(root)]
    while stack:
        directory = stack.pop()
        if _is_link(directory):
            raise CorpusError(f"{directory.relative_to(root).as_posix() or '.'}: fixture directories must not be links")
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            if _is_link(path):
                raise CorpusError(f"{relative}: fixture entries must not be symlinks or reparse points")
            if entry.is_dir(follow_symlinks=False):
                stack.append(path)
            elif entry.is_file(follow_symlinks=False):
                yield relative, path
            else:
                raise CorpusError(f"{relative}: unsupported file type")


def load_corpus(root):
    root = Path(root)
    if not root.is_dir():
        raise CorpusError(f"fixture root does not exist: {root}")
    try:
        families_data, families_sha = _read_json(root / FAMILIES_FILE, FAMILIES_FILE)
        coverage_data, coverage_sha = _read_json(root / COVERAGE_FILE, COVERAGE_FILE)
        families = schema.validate_families(FAMILIES_FILE, families_data)["families"]
        required = schema.validate_required_coverage(COVERAGE_FILE, coverage_data)["required"]
    except (MetadataError, FileNotFoundError) as error:
        raise CorpusError(str(error)) from None
    corpus = Corpus(root=root, families=families, required_tags=required)
    corpus.files[FAMILIES_FILE] = families_sha
    corpus.files[COVERAGE_FILE] = coverage_sha

    image_files = {}
    image_sources = []
    sequence_sources = []
    for relative, path in _walk(root):
        suffix = path.suffix.lower()
        if suffix not in ALLOWED_SUFFIXES or path.suffix != suffix:
            raise CorpusError(f"{relative}: file type is not allowed in the fixture corpus")
        parts = relative.split("/")
        if suffix in IMAGE_SUFFIXES:
            if len(parts) not in (1, 3) or (len(parts) == 3 and parts[0] != "results"):
                raise CorpusError(f"{relative}: images live at the legacy root or under results/<category>/")
            image_files[relative] = path
            continue
        if suffix == ".md":
            continue
        if len(parts) == 1:
            if relative in (FAMILIES_FILE, COVERAGE_FILE):
                continue
            if relative == LEGACY_FILE:
                corpus.files[LEGACY_FILE] = hashlib.sha256(path.read_bytes()).hexdigest()
                continue
            raise CorpusError(f"{relative}: unexpected metadata at the fixture root")
        if len(parts) != 3:
            raise CorpusError(f"{relative}: records live at <family>/<category>/<name>.json")
        family, category = parts[0], parts[1]
        if family not in families:
            raise CorpusError(f"{relative}: unknown fixture family {family!r}")
        if category not in families[family]["categories"]:
            raise CorpusError(f"{relative}: unknown category {category!r} for family {family!r}")
        if families[family]["status"] != "implemented":
            raise CorpusError(
                f"{relative}: family {family!r} is reserved and has no replay adapter; "
                "a record here cannot be run and is not skipped")
        (sequence_sources if category == "sequences" else image_sources).append((relative, path, category))

    if len(image_sources) + len(sequence_sources) > MAX_RECORDS:
        raise CorpusError(f"corpus exceeds {MAX_RECORDS} records")
    known_tags = set(required)
    ids = set()
    referenced = {}
    for relative, path, category in image_sources:
        record, digest = _read_json(path, relative)
        try:
            schema.validate_image_record(relative, record, category, known_tags)
        except MetadataError as error:
            raise CorpusError(str(error)) from None
        if record["id"] in ids:
            raise CorpusError(f"{relative}: duplicate fixture id {record['id']!r}")
        ids.add(record["id"])
        image = record["input"]
        if image["path"] in referenced:
            raise CorpusError(
                f"{relative}: image {image['path']!r} is already described by {referenced[image['path']]}")
        referenced[image["path"]] = relative
        try:
            file_path = resolve_fixture_file(root, image["path"])
        except FixturePathError as error:
            raise CorpusError(f"{relative}: {error}") from None
        size = file_path.stat().st_size
        if size != image["bytes"]:
            raise CorpusError(f"{relative}: {image['path']} is {size} bytes, metadata says {image['bytes']}")
        data = file_path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != image["sha256"]:
            raise CorpusError(f"{relative}: {image['path']} SHA-256 is {actual}, metadata says {image['sha256']}")
        try:
            decoded = decode_image(data, image["format"])
        except ImageError as error:
            raise CorpusError(f"{relative}: {image['path']} does not decode: {error}") from None
        if (decoded.width, decoded.height) != (image["width"], image["height"]):
            raise CorpusError(
                f"{relative}: {image['path']} is {decoded.width}x{decoded.height}, "
                f"metadata says {image['width']}x{image['height']}")
        corpus.image_bytes += size
        corpus.files[relative] = digest
        corpus.files[image["path"]] = actual
        corpus.images[record["id"]] = record
        corpus.cases.append(Case(record["id"], "image", relative, record, digest))
    if corpus.image_bytes > MAX_CORPUS_IMAGE_BYTES:
        raise CorpusError(f"corpus images total {corpus.image_bytes} bytes; the limit is {MAX_CORPUS_IMAGE_BYTES}")
    orphans = sorted(set(image_files) - set(referenced))
    if orphans:
        raise CorpusError(f"images without a fixture record: {', '.join(orphans)}")

    for relative, path, _ in sequence_sources:
        record, digest = _read_json(path, relative)
        try:
            schema.validate_sequence_record(relative, record, known_tags, corpus.images)
        except MetadataError as error:
            raise CorpusError(str(error)) from None
        if record["id"] in ids:
            raise CorpusError(f"{relative}: duplicate fixture id {record['id']!r}")
        ids.add(record["id"])
        corpus.files[relative] = digest
        corpus.sequences[record["id"]] = record
        corpus.cases.append(Case(record["id"], "sequence", relative, record, digest))

    if not corpus.cases:
        raise CorpusError("the T1 corpus is empty")
    covered = {tag for case in corpus.cases for tag in case.record["coverage"]}
    missing = sorted(set(required) - covered)
    if missing:
        raise CorpusError(f"required coverage has no fixture: {', '.join(missing)}")
    corpus.cases.sort(key=lambda case: case.id)
    return corpus
