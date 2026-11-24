import hashlib
import os
import struct
import uuid
from fractions import Fraction
from pathlib import Path

import numpy as np
import tifffile

from . import __version__


def rational(value):
    result = Fraction(value).limit_denominator(1_000_000)
    return result.numerator, result.denominator


def append_exif(path, metadata, width, height):
    entries = [
        (33434, 5, 1, struct.pack("<II", *rational(metadata.exposure))),
        (33437, 5, 1, struct.pack("<II", *rational(metadata.aperture))),
        (34855, 3, 1, struct.pack("<H", min(metadata.iso, 65535))),
        (34864, 3, 1, struct.pack("<H", 3)),
        (34867, 4, 1, struct.pack("<I", metadata.iso)),
        (36864, 7, 4, b"0231"),
        (37386, 5, 1, struct.pack("<II", *rational(metadata.focal_length))),
        (40960, 7, 4, b"0100"),
        (40961, 3, 1, struct.pack("<H", 65535)),
        (40962, 4, 1, struct.pack("<I", width)),
        (40963, 4, 1, struct.pack("<I", height)),
    ]
    with Path(path).open("r+b") as stream:
        header = stream.read(8)
        if header[:4] != b"II*\0":
            raise ValueError("Expected little-endian classic TIFF")
        root_offset = struct.unpack_from("<I", header, 4)[0]
        stream.seek(root_offset)
        count = struct.unpack("<H", stream.read(2))[0]
        original = [stream.read(12) for _ in range(count)]
        following = stream.read(4)
        stream.seek(0, os.SEEK_END)
        if stream.tell() % 2:
            stream.write(b"\0")
        exif_offset = stream.tell()
        directory_length = 6 + len(entries) * 12
        values = bytearray()
        directory = bytearray(struct.pack("<H", len(entries)))
        for tag, kind, length, payload in entries:
            if len(payload) > 4:
                pointer = exif_offset + directory_length + len(values)
                values.extend(payload)
                payload = struct.pack("<I", pointer)
            directory.extend(struct.pack("<HHI", tag, kind, length) + payload.ljust(4, b"\0"))
        stream.write(directory + bytes(4) + values)
        new_root = stream.tell()
        original.append(struct.pack("<HHII", 34665, 4, 1, exif_offset))
        original.sort(key=lambda entry: struct.unpack_from("<H", entry)[0])
        stream.write(struct.pack("<H", len(original)) + b"".join(original) + following)
        stream.seek(4)
        stream.write(struct.pack("<I", new_root))


def write_frame(destination, raw, metadata, profile, rate, reel):
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite {destination}")
    if raw.dtype != np.uint16 or raw.ndim != 2 or any(size % 2 for size in raw.shape):
        raise ValueError("Expected an even-sized uint16 Bayer raster")
    if raw.max() > 16383:
        raise ValueError("Decoded values exceed validated 14-bit range")
    metadata.validate(raw.shape, profile.white)
    height, width = raw.shape
    left, top, crop_width, crop_height = metadata.crop
    neutral = tuple(number for value in metadata.neutral for number in rational(value))
    if not reel.isascii():
        reel = "NRAW-" + hashlib.sha256(reel.encode("utf-8")).hexdigest()[:16]
    tags = [
        (254, 4, 1, 0, False),
        (271, 2, 0, "NIKON CORPORATION", False),
        (272, 2, 0, profile.model, False),
        (274, 3, 1, metadata.orientation, False),
        (33421, 3, 2, (2, 2), False),
        (33422, 1, 4, bytes((0, 1, 1, 2)), False),
        (50706, 1, 4, bytes((1, 4, 0, 0)), False),
        (50707, 1, 4, bytes((1, 4, 0, 0)), False),
        (50708, 2, 0, profile.unique_model, False),
        (50710, 1, 3, bytes((0, 1, 2)), False),
        (50711, 3, 1, 1, False),
        (50713, 3, 2, (2, 2), False),
        (50714, 5, 4, tuple(number for value in metadata.black for number in (value, 1)), False),
        (50717, 4, 1, profile.white, False),
        (50718, 5, 2, (1, 1, 1, 1), False),
        (50719, 4, 2, (left, top), False),
        (50720, 4, 2, (crop_width, crop_height), False),
        (50728, 5, 3, neutral, False),
        (50829, 4, 4, (0, 0, height, width), False),
        (51044, 10, 1, (rate.numerator, rate.denominator), False),
        (51081, 2, 0, reel, False),
        *profile.tags,
    ]
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
    try:
        tifffile.imwrite(temporary, raw, byteorder="<", photometric=32803,
                         metadata=None, compression=None, rowsperstrip=min(height, 128),
                         software=f"NRAW to CinemaDNG {__version__}", extratags=tags)
        append_exif(temporary, metadata, width, height)
        with tifffile.TiffFile(temporary) as document:
            page = document.pages[0]
            restored = page.asarray()
            if not np.array_equal(raw, restored):
                raise RuntimeError("DNG pixel round-trip verification failed")
            if tuple(page.tags[50714].value) != tuple(number for value in metadata.black for number in (value, 1)):
                raise RuntimeError("DNG black-level verification failed")
            if page.tags[51044].value != (rate.numerator, rate.denominator):
                raise RuntimeError("DNG frame-rate verification failed")
            for code, _, _, value, _ in profile.tags:
                if not np.array_equal(np.asarray(page.tags[code].value), np.asarray(value)):
                    raise RuntimeError(f"DNG profile tag {code} changed")
        with temporary.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        os.rename(temporary, destination)
        return digest
    finally:
        if temporary.exists():
            temporary.unlink()
