import math
import struct
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class FrameMetadata:
    black: tuple
    neutral: tuple
    crop: tuple
    iso: int
    exposure: float
    aperture: float
    focal_length: float
    orientation: int

    @classmethod
    def read(cls, sample):
        if len(sample) < 1026 or sample[4:8] != b"NRAW":
            raise ValueError("Invalid NRAW sample")
        header = sample[:1024]
        records = {}
        position = 0
        while True:
            position = header.find(b"NRMT", position)
            if position < 0:
                break
            if position < 4:
                raise ValueError("Invalid NRMT record")
            size = struct.unpack_from(">I", header, position - 4)[0]
            if size < 13 or position - 4 + size > len(header):
                raise ValueError("Truncated NRMT record")
            value = header[position + 4:position - 4 + size]
            key = struct.unpack_from(">HH", value)
            if key in records:
                raise ValueError("Duplicate NRMT record")
            records[key] = value[5:]
            position += size - 4
        if records.get((0x110, 41730)) != b"\0\2\0\2\0\1\1\2":
            raise ValueError("Only explicitly labelled RGGB frames are supported")
        stream = records[(0x200, 1)]
        if stream[:2] != b"II":
            raise ValueError("Unsupported Nikon frame byte order")
        units = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}
        position = 2
        maker = {}
        while position < len(stream):
            tag, kind, count = struct.unpack_from("<HHI", stream, position)
            position += 8
            length = units[kind] * count
            value = stream[position:position + length]
            if len(value) != length or tag in maker:
                raise ValueError("Invalid Nikon frame metadata")
            maker[tag] = value
            position += length
        black = struct.unpack("<4H", maker[0x3D])
        crop = struct.unpack("<4H", maker[0x45])
        balance = struct.unpack("<8I", maker[0x0C])
        if any(value == 0 for value in balance):
            raise ValueError("Invalid white-balance rational")
        gains = [Fraction(balance[index], balance[index + 1]) for index in range(0, 8, 2)]
        if gains[2] != gains[3]:
            raise ValueError("Unequal green WB gains need an explicit four-channel calibration")
        neutral = (gains[2] / gains[0], Fraction(1), gains[2] / gains[1])
        values = [struct.unpack(">f", records[(0x110, tag)])[0] for tag in (33434, 33437, 37386)]
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("Invalid exposure or lens metadata")
        orientation = struct.unpack(">H", records.get((0x110, 274), b"\0\1"))[0]
        if not 1 <= orientation <= 8:
            raise ValueError("Invalid orientation")
        return cls(black, neutral, crop, struct.unpack(">I", records[(0x110, 34866)])[0], *values, orientation)

    def validate(self, shape, white):
        height, width = shape
        left, top, crop_width, crop_height = self.crop
        if min(crop_width, crop_height) <= 0 or left + crop_width > width or top + crop_height > height:
            raise ValueError("Source crop outside decoded raster")
        if any(not 0 <= value < white for value in self.black):
            raise ValueError("Invalid per-frame black level")
