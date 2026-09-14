"""Strict decoders for T1 fixture images: binary P6 PPM and PNG RGB8.

Production classifies BGRA buffers, so the only conversion allowed is an exact
RGB -> BGRA copy with alpha 255. There is no resize, gamma, colour management,
interpolation or normalisation, and any input these decoders cannot represent
exactly is refused rather than approximated.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 3840 * 2160

PPM_WHITESPACE = frozenset(b" \t\n\r\x0b\x0c")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Ancillary chunks carry text, EXIF, timestamps or colour management. None of
# them is pixel evidence, and colour chunks would invite a conversion.
PNG_ALLOWED_CHUNKS = frozenset((b"IHDR", b"IDAT", b"IEND"))


class ImageError(ValueError):
    """Fixture bytes are not an exactly decodable image."""


@dataclass(frozen=True)
class DecodedImage:
    format: str
    width: int
    height: int
    bgra: bytes


def _check_dimensions(width, height):
    if width <= 0 or height <= 0:
        raise ImageError(f"image dimensions must be positive: {width}x{height}")
    if width * height > MAX_PIXELS:
        raise ImageError(f"image has {width * height} pixels; the limit is {MAX_PIXELS}")


def rgb_to_bgra(rgb, width, height):
    pixels = width * height
    if len(rgb) != pixels * 3:
        raise ImageError(f"RGB buffer is {len(rgb)} bytes; {pixels * 3} expected")
    out = bytearray(pixels * 4)
    out[0::4] = rgb[2::3]
    out[1::4] = rgb[1::3]
    out[2::4] = rgb[0::3]
    out[3::4] = b"\xff" * pixels
    return bytes(out)


def decode_ppm(data):
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError(f"PPM exceeds {MAX_IMAGE_BYTES} bytes")
    if not data.startswith(b"P6"):
        raise ImageError("PPM must be binary P6")
    position = 2
    fields = []
    while len(fields) < 3:
        if position >= len(data) or data[position] not in PPM_WHITESPACE:
            raise ImageError("PPM header field is not separated by whitespace")
        while position < len(data) and data[position] in PPM_WHITESPACE:
            position += 1
        if position < len(data) and data[position] == ord("#"):
            # Legal netpbm, but a free-text channel in a fixture header.
            raise ImageError("PPM header comments are not accepted")
        start = position
        while position < len(data) and 48 <= data[position] <= 57:
            position += 1
        if position == start or position - start > 7:
            raise ImageError("PPM header field is not a bounded decimal number")
        fields.append(int(data[start:position]))
    if position >= len(data) or data[position] not in PPM_WHITESPACE:
        raise ImageError("PPM maxval must be followed by exactly one whitespace byte")
    position += 1
    width, height, maxval = fields
    if maxval != 255:
        raise ImageError(f"PPM maxval must be 255, not {maxval}")
    _check_dimensions(width, height)
    expected = position + width * height * 3
    if len(data) != expected:
        # A CRLF checkout conversion lands here: it inserts bytes into the pixels.
        raise ImageError(
            f"PPM is {len(data)} bytes; header and {width}x{height} pixels need exactly {expected}")
    return DecodedImage("ppm", width, height, rgb_to_bgra(memoryview(data)[position:], width, height))


def _paeth(left, up, up_left):
    estimate = left + up - up_left
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_up_left = abs(estimate - up_left)
    if distance_left <= distance_up and distance_left <= distance_up_left:
        return left
    if distance_up <= distance_up_left:
        return up
    return up_left


def decode_png(data):
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError(f"PNG exceeds {MAX_IMAGE_BYTES} bytes")
    if not data.startswith(PNG_SIGNATURE):
        raise ImageError("PNG signature is missing")
    position = len(PNG_SIGNATURE)
    header = None
    idat = []
    idat_closed = False
    ended = False
    index = 0
    while position < len(data):
        if position + 12 > len(data):
            raise ImageError("PNG chunk is truncated")
        (length,) = struct.unpack(">I", data[position:position + 4])
        kind = bytes(data[position + 4:position + 8])
        end = position + 12 + length
        if length > MAX_IMAGE_BYTES or end > len(data):
            raise ImageError("PNG chunk length runs past the file")
        body = bytes(data[position + 8:position + 8 + length])
        (crc,) = struct.unpack(">I", data[position + 8 + length:end])
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise ImageError(f"PNG chunk {kind!r} fails its CRC")
        if index == 0 and kind != b"IHDR":
            raise ImageError("PNG must start with IHDR")
        if kind not in PNG_ALLOWED_CHUNKS:
            raise ImageError(f"PNG chunk {kind!r} is not accepted; only IHDR, IDAT and IEND are")
        if kind == b"IHDR":
            if header is not None or length != 13:
                raise ImageError("PNG IHDR must appear once with 13 bytes")
            width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", body)
            if (depth, colour, compression, filtering, interlace) != (8, 2, 0, 0, 0):
                raise ImageError(
                    "PNG must be 8-bit RGB (colour type 2), non-interlaced, standard compression and filtering")
            _check_dimensions(width, height)
            header = (width, height)
        elif kind == b"IDAT":
            if header is None or idat_closed:
                raise ImageError("PNG IDAT chunks must follow IHDR consecutively")
            idat.append(body)
        else:
            if length != 0:
                raise ImageError("PNG IEND must be empty")
            ended = True
            position = end
            break
        if idat and kind != b"IDAT":
            idat_closed = True
        position = end
        index += 1
    if not ended:
        raise ImageError("PNG has no IEND")
    if position != len(data):
        raise ImageError("PNG has data after IEND")
    if header is None or not idat:
        raise ImageError("PNG has no image data")
    width, height = header
    stride = width * 3
    expected = height * (stride + 1)
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(b"".join(idat), expected + 1)
    except zlib.error as error:
        raise ImageError(f"PNG image data is not valid zlib: {error}") from None
    if len(raw) != expected or inflater.unconsumed_tail or not inflater.eof or inflater.unused_data:
        raise ImageError("PNG image data does not inflate to exactly one scanline per row")
    rgb = bytearray(width * height * 3)
    previous = bytearray(stride)
    for row in range(height):
        offset = row * (stride + 1)
        method = raw[offset]
        line = bytearray(raw[offset + 1:offset + 1 + stride])
        if method == 1:
            for i in range(3, stride):
                line[i] = (line[i] + line[i - 3]) & 0xFF
        elif method == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif method == 3:
            for i in range(stride):
                left = line[i - 3] if i >= 3 else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif method == 4:
            for i in range(stride):
                left = line[i - 3] if i >= 3 else 0
                up_left = previous[i - 3] if i >= 3 else 0
                line[i] = (line[i] + _paeth(left, previous[i], up_left)) & 0xFF
        elif method != 0:
            raise ImageError(f"PNG row {row} uses unknown filter type {method}")
        rgb[row * stride:(row + 1) * stride] = line
        previous = line
    return DecodedImage("png", width, height, rgb_to_bgra(rgb, width, height))


def decode_image(data, declared_format):
    if declared_format == "ppm":
        return decode_ppm(data)
    if declared_format == "png":
        return decode_png(data)
    raise ImageError(f"unsupported image format: {declared_format!r}")
