import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from fractions import Fraction
from unittest.mock import patch

import numpy as np
import tifffile

from nraw_cinema.container import Clip, boxes
from nraw_cinema.convert import convert, selection
from nraw_cinema.dng import write_frame
from nraw_cinema.metadata import FrameMetadata
from nraw_cinema.profile import CameraProfile


def fixture_profile(root, model="NIKON Z 8"):
    path = root / "profile.dng"
    matrix = (10000, 10000, 0, 1, 0, 1, 0, 1, 10000, 10000, 0, 1, 0, 1, 0, 1, 10000, 10000)
    tifffile.imwrite(path, np.full((16, 20), 1200, np.uint16), photometric=32803, metadata=None,
                     extratags=[(254, 4, 1, 0, False), (272, 2, 0, model, False),
                                (50706, 1, 4, b"\1\4\0\0", False), (50708, 2, 0, model, False),
                                (50717, 4, 1, 16000, False), (50721, 10, 9, matrix, False),
                                (50778, 3, 1, 21, False)])
    return CameraProfile.read(path)


def fixture_metadata():
    return FrameMetadata((1001, 1002, 1003, 1004), (Fraction(1, 2), Fraction(1), Fraction(2, 3)),
                         (3, 3, 14, 10), 800, 1 / 50, 4, 24, 1)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.profile = fixture_profile(self.root)
        self.metadata = fixture_metadata()
        self.raw = np.arange(320, dtype=np.uint16).reshape(16, 20) + 1000

    def tearDown(self):
        self.temporary.cleanup()

    def test_pixel_and_tag_roundtrip(self):
        destination = self.root / "frame.dng"
        write_frame(destination, self.raw, self.metadata, self.profile, Fraction(30000, 1001), "clip")
        with tifffile.TiffFile(destination) as document:
            page = document.pages[0]
            np.testing.assert_array_equal(page.asarray(), self.raw)
            self.assertEqual(page.tags[33422].value, b"\0\1\1\2")
            self.assertEqual(page.tags[50719].value, (3, 3))
            self.assertEqual(page.tags[50829].value, (0, 0, 16, 20))
            self.assertEqual(page.tags[50728].value, (1, 2, 1, 1, 2, 3))
            self.assertEqual(page.tags[51044].value, (30000, 1001))
            self.assertEqual(page.tags[34665].value["ISOSpeedRatings"], 800)

    def test_no_overwrite(self):
        destination = self.root / "frame.dng"
        destination.write_bytes(b"protected")
        with self.assertRaises(FileExistsError):
            write_frame(destination, self.raw, self.metadata, self.profile, Fraction(25), "clip")
        self.assertEqual(destination.read_bytes(), b"protected")

    def test_unicode_reel(self):
        destination = self.root / "夜景 片段.dng"
        write_frame(destination, self.raw, self.metadata, self.profile, Fraction(25), "夜景 片段")
        with tifffile.TiffFile(destination) as document:
            self.assertTrue(document.pages[0].tags[51081].value.startswith("NRAW-"))

    def test_wrong_model(self):
        with self.assertRaises(ValueError):
            self.profile.match("NIKON Z6_3")

    def test_invalid_crop(self):
        with self.assertRaises(ValueError):
            self.metadata.validate((8, 8), 16000)

    def test_bitdepth_out_of_range(self):
        self.raw[0, 0] = 65535
        with self.assertRaises(ValueError):
            write_frame(self.root / "bad.dng", self.raw, self.metadata, self.profile, Fraction(25), "clip")

    def test_frame_selection(self):
        clip = Clip(self.root / "input.nev", tuple((0, 1) for _ in range(7)), Fraction(25), "NIKON Z 8", 1, 1)
        self.assertEqual(list(selection(clip, 1, 3, 2)), [1, 3, 5])
        for arguments in ((-1, None, 1), (0, 0, 1), (0, 9, 1), (0, None, 0)):
            with self.assertRaises(ValueError):
                selection(clip, *arguments)

    def test_malformed_atoms(self):
        import io
        for data in (b"123", struct.pack(">I4s", 3, b"moov"), struct.pack(">I4s", 100, b"moov")):
            with self.assertRaises(ValueError):
                list(boxes(io.BytesIO(data), 0, len(data)))

    def test_extended_atom(self):
        import io
        data = struct.pack(">I4sQ", 1, b"mdat", 20) + b"1234"
        self.assertEqual(list(boxes(io.BytesIO(data), 0, len(data))), [(b"mdat", 16, 20)])

    def test_metadata_rejects_non_nraw(self):
        with self.assertRaises(ValueError):
            FrameMetadata.read(bytes(2048))

    def test_resume_cancel_and_tamper(self):
        source = self.root / "input.nev"
        source.write_bytes(bytes(4096))
        identity = source.stat()
        clip = Clip(source, ((0, 2048), (2048, 2048)), Fraction(25), "NIKON Z 8", identity.st_size, identity.st_mtime_ns)
        output = self.root / "sequence"
        stop = threading.Event()
        events = []
        def event_callback(event):
            events.append(event)
            if event["event"] == "progress":
                stop.set()
        with patch("nraw_cinema.convert.Clip.open", return_value=clip), patch("nraw_cinema.convert.Decoder") as decoder, patch("nraw_cinema.convert.FrameMetadata.read", return_value=self.metadata):
            decoder.return_value.decode.return_value = (self.raw, 14)
            self.assertFalse(convert(source, "sdk", self.profile.source, output, stop=stop, emit=event_callback))
            self.assertEqual(len(list(output.glob("*.dng"))), 1)
            self.assertFalse((output / ".conversion.lock").exists())
            self.assertTrue(convert(source, "sdk", self.profile.source, output, resume=True))
            self.assertEqual(len(list(output.glob("*.dng"))), 2)
            with self.assertRaises(FileExistsError):
                convert(source, "sdk", self.profile.source, output)
            frame = output / "frame_00000000.dng"
            with frame.open("ab") as stream:
                stream.write(b"tampered")
            with self.assertRaises(ValueError):
                convert(source, "sdk", self.profile.source, output, resume=True)

    def test_lock_protection(self):
        source = self.root / "input.nev"
        clip = Clip(source, ((0, 2048),), Fraction(25), "NIKON Z 8", 2048, 1)
        output = self.root / "locked"
        output.mkdir()
        (output / ".conversion.lock").touch()
        with patch("nraw_cinema.convert.Clip.open", return_value=clip):
            with self.assertRaises(RuntimeError):
                convert(source, "sdk", self.profile.source, output)
        self.assertTrue((output / ".conversion.lock").exists())

    def test_sampled_sequence_has_contiguous_names(self):
        source = self.root / "input.nev"
        source.write_bytes(bytes(8192))
        identity = source.stat()
        clip = Clip(source, tuple((index * 2048, 2048) for index in range(4)), Fraction(50), "NIKON Z 8", identity.st_size, identity.st_mtime_ns)
        output = self.root / "sampled"
        with patch("nraw_cinema.convert.Clip.open", return_value=clip), patch("nraw_cinema.convert.Decoder") as decoder, patch("nraw_cinema.convert.FrameMetadata.read", return_value=self.metadata):
            decoder.return_value.decode.return_value = (self.raw, 14)
            convert(source, "sdk", self.profile.source, output, start=1, count=2, step=2)
        self.assertEqual(sorted(path.name for path in output.glob("*.dng")), ["frame_00000000.dng", "frame_00000001.dng"])
        with tifffile.TiffFile(output / "frame_00000001.dng") as document:
            self.assertEqual(document.pages[0].tags[51044].value, (25, 1))
        records = [json.loads(line) for line in (output / "frames.jsonl").read_text().splitlines()]
        self.assertEqual([record["frame"] for record in records], [1, 3])


if __name__ == "__main__":
    unittest.main()
