import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import rawpy
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nraw_cinema.container import Clip
from nraw_cinema.decoder import Decoder
from nraw_cinema.metadata import FrameMetadata
from nraw_cinema.profile import CameraProfile, digest_file


def main():
    parser = argparse.ArgumentParser(description="Independent LibRaw/raw-pixel and metadata verification")
    parser.add_argument("source", type=Path)
    parser.add_argument("sequence", type=Path)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    arguments = parser.parse_args()
    clip = Clip.open(arguments.source)
    profile = CameraProfile.read(arguments.profile)
    profile.match(clip.model)
    decoder = Decoder(arguments.sdk)
    job = json.loads((arguments.sequence / "conversion.json").read_text(encoding="utf-8"))
    entries = [json.loads(line) for line in (arguments.sequence / "frames.jsonl").read_text(encoding="utf-8").splitlines()]
    if len(entries) != job["count"]:
        raise ValueError("Sequence is not complete")
    for index, entry in enumerate(entries):
        if entry["frame"] != job["start"] + index * job["step"] or entry["file"] != f"frame_{index:08d}.dng":
            raise ValueError("Sequence ordering differs from the declared selection")
        sample = clip.read(entry["frame"])
        raw, _ = decoder.decode(sample)
        metadata = FrameMetadata.read(sample)
        path = arguments.sequence / entry["file"]
        if digest_file(path) != entry["sha256"] or hashlib.sha256(sample).hexdigest() != entry["sample_sha256"]:
            raise ValueError("Input/output hash mismatch")
        with rawpy.imread(str(path)) as image:
            np.testing.assert_array_equal(image.raw_image, raw)
            np.testing.assert_array_equal(image.raw_pattern, [[0, 1], [3, 2]])
            np.testing.assert_array_equal(image.black_level_per_channel, [metadata.black[channel] for channel in (0, 1, 3, 2)])
            np.testing.assert_allclose(image.camera_whitebalance[:3], [float(1 / value) for value in metadata.neutral], rtol=1e-6, atol=1e-6)
            if image.white_level != profile.white:
                raise ValueError("WhiteLevel mismatch")
        with tifffile.TiffFile(path) as document:
            page = document.pages[0]
            rate = clip.rate / job["step"]
            if page.tags[51044].value != (rate.numerator, rate.denominator):
                raise ValueError("FrameRate mismatch")
            for code, _, _, value, _ in profile.tags:
                np.testing.assert_array_equal(page.tags[code].value, value)
        print(json.dumps(dict(frame=entry["frame"], libraw_exact=True, metadata_exact=True)), flush=True)
    print(json.dumps(dict(success=True, count=len(entries))), flush=True)


if __name__ == "__main__":
    main()
