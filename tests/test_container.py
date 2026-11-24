import io
from pathlib import Path
import struct
import tempfile
import unittest

from nraw_cinema.container import Clip
from nraw_cinema.metadata import FrameMetadata


def atom(kind, payload):
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def fullbox(kind, payload):
    return atom(kind, bytes(4) + payload)


def nev_bytes(variable_rate=False, outside=False, model="NIKON Z 8"):
    sample = bytes(2048)
    media = atom(b"mdat", sample + sample)
    model_bytes = model.encode() + b"\0"
    nctg = atom(b"NCTG", struct.pack(">IHH", 2, 2, len(model_bytes)) + model_bytes)
    metadata = atom(b"udta", atom(b"NCDT", nctg))
    sizes = fullbox(b"stsz", struct.pack(">II", len(sample), 2))
    chunks = fullbox(b"stsc", struct.pack(">IIII", 1, 1, 2, 1))
    offsets = fullbox(b"stco", struct.pack(">II", 1, 999999 if outside else 8))
    description = fullbox(b"stsd", struct.pack(">I", 1) + atom(b"NRAW", bytes(78)))
    runs = [(1, 1001), (1, 1002)] if variable_rate else [(2, 1001)]
    timing = fullbox(b"stts", struct.pack(">I", len(runs)) + b"".join(struct.pack(">II", *run) for run in runs))
    table = atom(b"stbl", description + sizes + chunks + offsets + timing)
    header = fullbox(b"mdhd", struct.pack(">IIIIHH", 0, 0, 30000, 2002, 0, 0))
    track = atom(b"trak", atom(b"mdia", header + atom(b"minf", table)))
    return media + atom(b"moov", metadata + track)


def frame_header(cfa=b"\0\2\0\2\0\1\1\2", unequal_green=False):
    def record(group, tag, value):
        return atom(b"NRMT", struct.pack(">HHB", group, tag, 0) + value)
    def maker_record(tag, kind, count, value):
        return struct.pack("<HHI", tag, kind, count) + value
    wb = (2, 1, 3, 1, 1, 1, 2 if unequal_green else 1, 1)
    maker = b"II" + maker_record(0x3D, 3, 4, struct.pack("<4H", 1000, 1001, 1002, 1003))
    maker += maker_record(0x45, 3, 4, struct.pack("<4H", 4, 4, 64, 32))
    maker += maker_record(0x0C, 5, 4, struct.pack("<8I", *wb))
    payload = record(0x200, 1, maker) + record(0x110, 41730, cfa)
    payload += record(0x110, 34866, struct.pack(">I", 800))
    for tag, value in ((33434, 0.02), (33437, 4), (37386, 24)):
        payload += record(0x110, tag, struct.pack(">f", value))
    result = struct.pack(">I4s", 2048, b"NRAW") + payload
    return result.ljust(1024, b"\0") + b"\xff\x10" + bytes(1022)


class ContainerTests(unittest.TestCase):
    def test_index_and_rate(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.nev"
            source.write_bytes(nev_bytes())
            clip = Clip.open(source)
            self.assertEqual(str(clip.rate), "30000/1001")
            self.assertEqual(clip.samples, ((8, 2048), (2056, 2048)))
            self.assertEqual(len(clip.read(1)), 2048)
            source.write_bytes(nev_bytes() + b"changed")
            with self.assertRaises(ValueError):
                clip.read(0)

    def test_invalid_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.nev"
            for options in ({"variable_rate": True}, {"outside": True}, {"model": "UNTESTED CAMERA"}):
                source.write_bytes(nev_bytes(**options))
                with self.assertRaises(ValueError):
                    Clip.open(source)

    def test_frame_metadata(self):
        metadata = FrameMetadata.read(frame_header())
        self.assertEqual(metadata.black, (1000, 1001, 1002, 1003))
        self.assertEqual([str(value) for value in metadata.neutral], ["1/2", "1", "1/3"])
        self.assertEqual(metadata.crop, (4, 4, 64, 32))
        self.assertEqual(metadata.iso, 800)

    def test_reject_unsupported_cfa(self):
        with self.assertRaises(ValueError):
            FrameMetadata.read(frame_header(cfa=b"\0\2\0\2\2\1\1\0"))

    def test_reject_unequal_greens(self):
        with self.assertRaises(ValueError):
            FrameMetadata.read(frame_header(unequal_green=True))


if __name__ == "__main__":
    unittest.main()
