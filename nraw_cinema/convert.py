import hashlib
import json
import os
import shutil
import threading
from pathlib import Path

from . import __version__
from .container import Clip
from .decoder import Decoder, SDK_SHA256
from .dng import write_frame
from .metadata import FrameMetadata
from .profile import CameraProfile, digest_file


def selection(clip, start=0, count=None, step=1):
    if start < 0 or start >= len(clip.samples) or step < 1 or (count is not None and count < 1):
        raise ValueError("Invalid frame selection (start is zero-based; count and step must be positive)")
    frames = range(start, len(clip.samples), step)
    if count is not None:
        if count > len(frames):
            raise ValueError("Requested count extends beyond the clip")
        frames = frames[:count]
    return frames


def inspect(source, sdk, profile_path, start=0, count=None, step=1):
    clip = Clip.open(source)
    profile = CameraProfile.read(profile_path)
    profile.match(clip.model)
    frames = selection(clip, start, count, step)
    sample = clip.read(frames[0])
    metadata = FrameMetadata.read(sample)
    raw, _ = Decoder(sdk).decode(sample)
    metadata.validate(raw.shape, profile.white)
    return dict(model=clip.model, frames=len(clip.samples), selected=len(frames),
                raw_size=list(raw.shape[::-1]), crop=list(metadata.crop),
                source_rate=str(clip.rate), output_rate=str(clip.rate / step),
                estimated_bytes=(raw.nbytes + 2 * 1024 * 1024) * len(frames),
                white=profile.white, black=metadata.black,
                profile=profile.unique_model, profile_sha256=profile.sha256)


def convert(source, sdk, profile_path, output, start=0, count=None, step=1,
            resume=False, threads=2, stop=None, emit=None):
    emit = emit or (lambda event: None)
    stop = stop or threading.Event()
    clip = Clip.open(source)
    profile = CameraProfile.read(profile_path)
    profile.match(clip.model)
    frames = selection(clip, start, count, step)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    lock = output / ".conversion.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RuntimeError("Output is locked; if a previous process crashed, confirm it has exited before removing .conversion.lock") from error
    os.close(descriptor)
    try:
        job = dict(version=__version__, source=str(clip.source), source_bytes=clip.size,
                   source_mtime_ns=clip.modified_ns, profile_sha256=profile.sha256,
                   sdk_sha256=SDK_SHA256, start=start, count=len(frames), step=step,
                   source_rate=str(clip.rate), output_rate=str(clip.rate / step),
                   model=clip.model, white_from_reference=profile.white,
                   processing="Unmodified decoded CFA; frame black/WB/crop; imported reference camera profile",
                   audio="not exported", timecode="not synthesized or copied")
        job_path, receipt_path = output / "conversion.json", output / "frames.jsonl"
        existing = {}
        if job_path.exists():
            if not resume or json.loads(job_path.read_text(encoding="utf-8")) != job:
                raise FileExistsError("Existing output does not match this job, or Resume is not enabled")
            if receipt_path.exists():
                for line in receipt_path.read_text(encoding="utf-8").splitlines():
                    entry = json.loads(line)
                    if entry["frame"] in existing:
                        raise ValueError("Duplicate frame receipt")
                    existing[entry["frame"]] = entry
        else:
            if any(path.name != lock.name for path in output.iterdir()):
                raise FileExistsError("Choose an empty output sequence folder")
            job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        decoder = Decoder(sdk, threads)
        completed = 0
        for output_index, frame in enumerate(frames):
            if stop.is_set():
                emit(dict(event="cancelled", completed=completed, total=len(frames)))
                return False
            destination = output / f"frame_{output_index:08d}.dng"
            if frame in existing:
                receipt = existing[frame]
                if destination.name != receipt["file"] or not destination.is_file() or digest_file(destination) != receipt["sha256"]:
                    raise ValueError(f"Resume verification failed: {destination.name}")
                if hashlib.sha256(clip.read(frame)).hexdigest() != receipt["sample_sha256"]:
                    raise ValueError("Source sample differs from the saved receipt")
            else:
                if destination.exists():
                    raise FileExistsError(f"Unverified existing output: {destination.name}")
                sample = clip.read(frame)
                metadata = FrameMetadata.read(sample)
                raw, depth = decoder.decode(sample)
                remaining = len(frames) - completed - sum(index > frame for index in existing)
                required = (raw.nbytes + 2 * 1024 * 1024) * max(1, remaining)
                if shutil.disk_usage(output).free < required + 256 * 1024 * 1024:
                    raise OSError(f"Insufficient disk space: need approximately {required / 1024**3:.2f} GiB")
                digest = write_frame(destination, raw, metadata, profile, clip.rate / step, clip.source.stem)
                receipt = dict(frame=frame, output_index=output_index, file=destination.name, bytes=destination.stat().st_size,
                               sha256=digest, sample_sha256=hashlib.sha256(sample).hexdigest(),
                               raw_sha256=hashlib.sha256(memoryview(raw)).hexdigest(), depth=depth,
                               black=metadata.black, neutral=[str(value) for value in metadata.neutral],
                               crop=metadata.crop, iso=metadata.iso, exposure=metadata.exposure,
                               aperture=metadata.aperture, focal_length=metadata.focal_length)
                with receipt_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(receipt, ensure_ascii=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            completed += 1
            emit(dict(event="progress", frame=frame, completed=completed, total=len(frames), file=str(destination)))
        emit(dict(event="complete", completed=completed, output=str(output)))
        return True
    finally:
        lock.unlink(missing_ok=True)
