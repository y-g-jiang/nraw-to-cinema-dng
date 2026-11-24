import struct
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


def boxes(stream, start, end):
    position = start
    while position < end:
        if end - position < 8:
            raise ValueError("Truncated movie atom")
        stream.seek(position)
        size, kind = struct.unpack(">I4s", stream.read(8))
        header = 8
        if size == 1:
            extended = stream.read(8)
            if len(extended) != 8:
                raise ValueError("Truncated extended atom")
            size = struct.unpack(">Q", extended)[0]
            header = 16
        elif size == 0:
            size = end - position
        if size < header or position + size > end:
            raise ValueError("Movie atom outside parent")
        yield kind, position + header, position + size
        position += size


def child(stream, parent, kind):
    found = [item for item in boxes(stream, parent[1], parent[2]) if item[0] == kind]
    if len(found) != 1:
        raise ValueError(f"Expected one {kind!r} atom")
    return found[0]


def body(stream, atom, maximum=64 * 1024 * 1024):
    size = atom[2] - atom[1]
    if size > maximum:
        raise ValueError("Metadata atom too large")
    stream.seek(atom[1])
    value = stream.read(size)
    if len(value) != size:
        raise ValueError("Truncated metadata atom")
    return value


def camera_tags(stream, start, end, depth=0):
    if depth > 8:
        raise ValueError("Movie metadata nesting too deep")
    result = {}
    for atom in boxes(stream, start, end):
        if atom[0] in (b"moov", b"udta", b"NCDT"):
            result.update(camera_tags(stream, atom[1], atom[2], depth + 1))
        elif atom[0] == b"NCTG":
            data = body(stream, atom, 4 * 1024 * 1024)
            position = 0
            units = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
            while position + 8 <= len(data):
                tag, kind, count = struct.unpack_from(">IHH", data, position)
                if kind not in units:
                    raise ValueError("Unknown Nikon container metadata type")
                length = units[kind] * count
                value = data[position + 8:position + 8 + length]
                if len(value) != length:
                    raise ValueError("Truncated Nikon container metadata")
                if kind == 2:
                    result[tag] = value.rstrip(b"\0").decode("utf-8", errors="replace")
                position += 8 + length
    return result


@dataclass(frozen=True)
class Clip:
    source: Path
    samples: tuple
    rate: Fraction
    model: str
    size: int
    modified_ns: int

    @classmethod
    def open(cls, source):
        source = Path(source).resolve()
        if source.suffix.lower() != ".nev":
            raise ValueError("Input must be a .NEV file")
        identity = source.stat()
        with source.open("rb") as stream:
            top = list(boxes(stream, 0, identity.st_size))
            movie = next((atom for atom in top if atom[0] == b"moov"), None)
            if movie is None or any(atom[0] == b"moof" for atom in top):
                raise ValueError("Fragmented or incomplete NEV is not supported")
            payloads = [atom for atom in top if atom[0] == b"mdat"]
            tracks = []
            for track in boxes(stream, movie[1], movie[2]):
                if track[0] != b"trak":
                    continue
                media = child(stream, track, b"mdia")
                table = child(stream, child(stream, media, b"minf"), b"stbl")
                description = child(stream, table, b"stsd")
                if any(atom[0] == b"NRAW" for atom in boxes(stream, description[1] + 8, description[2])):
                    tracks.append((media, table))
            if len(tracks) != 1:
                raise ValueError("Expected exactly one NRAW video track")
            media, table = tracks[0]
            sizes_data = body(stream, child(stream, table, b"stsz"))
            fixed_size, count = struct.unpack_from(">II", sizes_data, 4)
            if not 0 < count <= 5_000_000:
                raise ValueError("Invalid video frame count")
            if not fixed_size and len(sizes_data) != 12 + count * 4:
                raise ValueError("Invalid sample size table")
            sizes = [fixed_size] * count if fixed_size else struct.unpack_from(f">{count}I", sizes_data, 12)
            chunk_data = body(stream, child(stream, table, b"stsc"))
            row_count = struct.unpack_from(">I", chunk_data, 4)[0]
            if not row_count or len(chunk_data) != 8 + 12 * row_count:
                raise ValueError("Invalid chunk map")
            rows = list(struct.iter_unpack(">III", chunk_data[8:]))
            if rows[0][0] != 1 or any(row[1] == 0 or row[2] != 1 for row in rows):
                raise ValueError("Unsupported sample description or chunk map")
            if any(current[0] <= previous[0] for previous, current in zip(rows, rows[1:])):
                raise ValueError("Unordered chunk map")
            offsets_atom = next((atom for atom in boxes(stream, table[1], table[2]) if atom[0] in (b"stco", b"co64")), None)
            if offsets_atom is None:
                raise ValueError("Missing chunk offsets")
            offsets_data = body(stream, offsets_atom)
            offset_count = struct.unpack_from(">I", offsets_data, 4)[0]
            width = 8 if offsets_atom[0] == b"co64" else 4
            if len(offsets_data) != 8 + width * offset_count or rows[-1][0] > offset_count:
                raise ValueError("Invalid chunk offsets")
            offsets = struct.unpack_from(f">{offset_count}{'Q' if width == 8 else 'I'}", offsets_data, 8)
            samples = []
            row_index = 0
            for chunk_index, offset in enumerate(offsets, 1):
                while row_index + 1 < len(rows) and rows[row_index + 1][0] <= chunk_index:
                    row_index += 1
                for _ in range(rows[row_index][1]):
                    if len(samples) >= count:
                        raise ValueError("Sample-count mismatch")
                    length = sizes[len(samples)]
                    if not 1026 <= length <= 256 * 1024 * 1024:
                        raise ValueError("Invalid compressed frame size")
                    if not any(atom[1] <= offset and offset + length <= atom[2] for atom in payloads):
                        raise ValueError("Video sample outside media data")
                    samples.append((offset, length))
                    offset += length
            if len(samples) != count:
                raise ValueError("Incomplete sample index")
            clock = body(stream, child(stream, media, b"mdhd"))
            if clock[0] not in (0, 1):
                raise ValueError("Unsupported media clock version")
            timescale = struct.unpack_from(">I", clock, 20 if clock[0] == 1 else 12)[0]
            timing = body(stream, child(stream, table, b"stts"))
            timing_count = struct.unpack_from(">I", timing, 4)[0]
            if len(timing) != 8 + timing_count * 8 or not timescale:
                raise ValueError("Invalid timing table")
            runs = list(struct.iter_unpack(">II", timing[8:]))
            if sum(row[0] for row in runs) != count or len({row[1] for row in runs}) != 1 or not runs[0][1]:
                raise ValueError("Variable-rate or incomplete video timing is not supported")
            rate = Fraction(timescale, runs[0][1])
            tags = camera_tags(stream, 0, identity.st_size)
            model = tags.get(2, "").strip()
            if model not in ("NIKON Z 8", "NIKON Z6_3"):
                raise ValueError(f"Unvalidated camera model: {model!r}; currently Z8 / Z6III only")
            return cls(source, tuple(samples), rate, model, identity.st_size, identity.st_mtime_ns)

    def read(self, frame):
        if not 0 <= frame < len(self.samples):
            raise IndexError(frame)
        identity = self.source.stat()
        if (identity.st_size, identity.st_mtime_ns) != (self.size, self.modified_ns):
            raise ValueError("Source changed since indexing")
        offset, length = self.samples[frame]
        with self.source.open("rb") as stream:
            stream.seek(offset)
            sample = stream.read(length)
        if len(sample) != length:
            raise ValueError("Truncated video sample")
        return sample
