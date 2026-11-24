import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile


PROFILE_TAGS = {
    50721, 50722, 50723, 50724, 50725, 50726, 50727, 50730,
    50778, 50779, 50931, 50932, 50934, 50936, 50937, 50938,
    50939, 50940, 50941, 50942, 50964, 50965, 50981, 50982,
    51107, 51108, 51109, 51110,
}


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pages(page):
    yield page
    if page.pages:
        for subpage in page.pages:
            yield from pages(subpage)


def normalized_model(value):
    return re.sub(r"[^A-Z0-9]", "", value.upper())


@dataclass(frozen=True)
class CameraProfile:
    source: Path
    sha256: str
    model: str
    unique_model: str
    white: int
    tags: tuple

    @classmethod
    def read(cls, path):
        path = Path(path).resolve()
        with tifffile.TiffFile(path) as document:
            root = document.pages[0]
            if 50706 not in root.tags:
                raise ValueError("Reference must be a camera-mosaic DNG, not a NEF or RGB image")
            raw_pages = [page for top in document.pages for page in pages(top)
                         if page.photometric == 32803 and page.tags.get(254, None) is not None and page.tags[254].value == 0]
            if len(raw_pages) != 1:
                raise ValueError("Reference must contain one primary CFA image")
            raw = raw_pages[0]
            if 50712 in raw.tags or 50712 in root.tags:
                raise ValueError("Reference with a linearization table is not supported")
            if 52531 in root.tags or 52525 in root.tags or 52543 in root.tags:
                raise ValueError("Triple-illuminant / gain-table / RGB-table profiles are not supported")
            model = root.tags[272].value
            unique = root.tags[50708].value
            white_values = np.asarray(raw.tags[50717].value).reshape(-1)
            if white_values.size != 1 or not 1024 < int(white_values[0]) <= 16383:
                raise ValueError("Reference must declare one compatible 14-bit saturation level")
            entries = []
            for code in sorted(PROFILE_TAGS):
                tag = root.tags.get(code)
                if tag is None:
                    continue
                if tag.count > 2_000_000 or tag.dtype not in (1, 2, 3, 4, 5, 9, 10, 11, 12):
                    raise ValueError("Unsupported or oversized profile tag")
                entries.append((code, int(tag.dtype), tag.count, tag.value, False))
            for code in (50721, 50722):
                if code not in root.tags:
                    if code == 50721:
                        raise ValueError("Reference has no ColorMatrix1")
                    continue
                matrix = np.asarray(root.tags[code].value, dtype=np.float64).reshape(9, 2)
                if np.any(matrix[:, 1] == 0):
                    raise ValueError("Invalid color matrix rational")
                matrix = (matrix[:, 0] / matrix[:, 1]).reshape(3, 3)
                if not np.all(np.isfinite(matrix)) or abs(np.linalg.det(matrix)) < 1e-8:
                    raise ValueError("Singular or nonfinite color matrix")
            if 50778 not in root.tags or (50722 in root.tags and 50779 not in root.tags):
                raise ValueError("Missing calibration illuminant")
            policy = root.tags.get(50941)
            if policy and policy.value not in (0, 1):
                raise ValueError("Profile embedding policy does not allow this use")
            white = int(white_values[0])
        return cls(path, digest_file(path), model, unique, white, tuple(entries))

    def match(self, model):
        if normalized_model(self.model) != normalized_model(model):
            raise ValueError(f"Profile camera {self.model!r} does not match clip camera {model!r}")
