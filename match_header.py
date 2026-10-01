"""Dormant, pure positive-only English Ranked Single header recognition.

Input is an unscaled BGR/BGRA crop (80, 40, 560, 100) of the evidenced
1920x1080 game layout. This is not general OCR or a multiclass recognizer.
No capture, state, clock, I/O, persistence or runtime callers are involved.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import zlib

import numpy as np

VERSION = "rank-single-header.v1"
SOURCE = "direct_header"
SHAPE = (60, 480)
MIN_GLOBAL = 0.95
MIN_GLYPH = 0.90
TEXT_TOP, TEXT_BOTTOM = 14, 42  # evidenced text rows; exclude UI border highlights

# Packed white-ink mask, mechanically extracted from the visually reviewed
# dev-header.png only. Its source/provenance and extraction are in the contract.
# Validation pixels never contribute to this template or its thresholds.
_PACKED = b"c-rmMJ&xNj5Cz~NSg_;`G#4SLYq20~1zT*HBkWbU*dk?)5C$pIa<N5N2%fx=thHk=k^@A9;U8I_Ma{f1(f_?z^tsc%_O-7+CS&^7<Y07W;z~K{$tNNq*Nn*Q<zx#*oOF~DxKMI(GL@XjwAo@##7*r;*A`!$tw;;_^@JmzZmp2-Y7d0B%O|x*k%vNoa722*ud98&w&beC*|XXir%m|8UH<@%<j=MeY^OAmQ@fRc$yWPy@&{Pk&UVOZGr5Ol&lcMaIF<t0TkBv)SXasXD()M_pf)WwY&KHL_trla<|#*C$h;NzL?N!$svXqgWmK#Czq1||Wu~|4pC}J%antt7@|4oOWjuVzBl=@rn`absvmz(fI!|Hn?^a~9uCS@>Y98msGE?_gc2L{G;7UR`o^SdaO=H{E>$V=*G(TPJlB*sDTQByK$v}-w&!ebKu+?q0YMt>qhXG%m?R3d=$-2ooN5t8*oN@cETxzwPmQk{f`?a_Bh}_cwJwiQONQ|A<#k!X@&nPo&zO&eu=AK4m1G#Fkvs-Q2tX>`V@IkJ=FOA?inhwiNZCf+>vSm{D96z#+;$ks?C~-qhOnaY&IGi(y2^q=X*dLY9QxAJoi=3?8J<s5vokEW=nIq;`<O@o4WsxNZJK}<f3R0C|*VzwSHpq2<XMYy*pV7id7zVYtMYN5)%&Wx?s$RZZ{O5=~Zn@gmzV@}Ref@3y1_%R{&;"
_REFERENCE = np.unpackbits(np.frombuffer(zlib.decompress(base64.b85decode(_PACKED)),
                                         dtype=np.uint8)).reshape(SHAPE).astype(bool)
_REFERENCE[:TEXT_TOP] = False
_REFERENCE[TEXT_BOTTOM:] = False
_REFERENCE.setflags(write=False)
_COLUMNS = np.flatnonzero(_REFERENCE.any(axis=0))
_BREAKS = np.flatnonzero(np.diff(_COLUMNS) > 1) + 1
_GLYPHS = tuple((int(group[0]), int(group[-1]) + 1)
                for group in np.split(_COLUMNS, _BREAKS))


@dataclass(frozen=True)
class HeaderRecognition:
    match_type: str = "unknown"
    match_format: str = "unknown"
    status: str = "failed"
    version: str = VERSION
    source: str = SOURCE
    reason: str = "insufficient_evidence"


def _ink(pixels):
    low = pixels.min(axis=2)
    high = pixels.max(axis=2)
    ink = (low >= 180) & ((high.astype(np.int16) - low) <= 50)
    ink[:TEXT_TOP] = False
    ink[TEXT_BOTTOM:] = False
    return ink


def _f1(reference, candidate):
    # Video/font antialiasing changes boundary pixels, even at native size.
    # Use mutual ink proximity within one pixel rather than exact bit identity.
    # The radius and acceptance thresholds are fixed; missing glyphs still fail.
    r, c = int(reference.sum()), int(candidate.sum())
    if not r or not c:
        return 0.0
    recall = int((reference & _neighborhood(candidate)).sum()) / r
    precision = int((candidate & _neighborhood(reference)).sum()) / c
    return 2 * recall * precision / (recall + precision) if recall + precision else 0.0


def _neighborhood(mask):
    padded = np.pad(mask, 1)
    height, width = mask.shape
    return np.logical_or.reduce([padded[y:y + height, x:x + width]
                                 for y in range(3) for x in range(3)])


def recognize_header(roi):
    """Recognize direct complete header evidence or abstain on both fields.

    Fixed bounded nine-position comparison tolerates one pixel of displacement.
    Every glyph, including punctuation and SINGLE, must independently match.
    Confidence is not a calibrated probability. No result is carried forward.
    """
    if (not isinstance(roi, np.ndarray) or roi.dtype != np.uint8 or roi.ndim != 3
            or roi.shape[:2] != SHAPE or roi.shape[2] not in (3, 4)):
        return HeaderRecognition(reason="invalid_geometry_or_format")
    if roi.shape[2] == 4 and not np.all(roi[:, :, 3] == 255):
        return HeaderRecognition(reason="nonopaque_evidence")
    ink = _ink(roi[:, :, :3])
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            candidate = np.zeros(SHAPE, dtype=bool)
            sy, ey = max(0, -dy), min(SHAPE[0], SHAPE[0] - dy)
            sx, ex = max(0, -dx), min(SHAPE[1], SHAPE[1] - dx)
            candidate[sy + dy:ey + dy, sx + dx:ex + dx] = ink[sy:ey, sx:ex]
            if _f1(_REFERENCE, candidate) < MIN_GLOBAL:
                continue
            if all(_f1(_REFERENCE[:, left:right], candidate[:, left:right]) >= MIN_GLYPH
                   for left, right in _GLYPHS):
                return HeaderRecognition("ranked", "single", "recognized", reason="complete_header")
    return HeaderRecognition()
