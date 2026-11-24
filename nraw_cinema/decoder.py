import ctypes
import hashlib
import os
from pathlib import Path

import numpy as np


SDK_SHA256 = "a3c3039647716ebfb254898568dbb0954925e35dcb76a48b24cc58af36ca4b31"


class Decoder:
    def __init__(self, sdk, threads=2):
        if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise RuntimeError("The decoder requires 64-bit Windows")
        if not 1 <= threads <= 32:
            raise ValueError("Decoder threads must be between 1 and 32")
        path = Path(sdk).resolve()
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != SDK_SHA256:
            raise ValueError("Unsupported REDR3D-x64.dll build; see README for the required SHA-256")
        self.search = os.add_dll_directory(str(path.parent))
        self.library = ctypes.CDLL(str(path))
        pointer, integer, size = ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t
        if self.function(0x3158F0, ctypes.c_ubyte, ctypes.c_uint)(threads) != 1:
            raise RuntimeError("SDK codec initialization failed")
        state = self.function(0x315860, pointer)()
        if not state:
            raise RuntimeError("SDK did not provide decoder state")
        self.format = pointer.from_address(state + 8).value
        self.decoder = pointer.from_address(state + 16).value
        self.input = self.function(0x1150, pointer)()
        self.output = self.function(0x1150, pointer)()
        self.options = self.function(0x1670, pointer)()
        self.parse = self.function(0x31B0, integer, pointer, size, pointer, pointer)
        self.configure = self.function(0x26C0, integer, pointer, pointer, pointer, pointer, pointer)
        self.width = self.function(0x11B0, integer, pointer)
        self.height = self.function(0x11C0, integer, pointer)
        self.depth = self.function(0x1080, integer, pointer)
        self.unpack = self.function(0x3210, integer, pointer, pointer, pointer, size, pointer, size, pointer, pointer, pointer)
        self.raw = None

    def function(self, offset, result, *arguments):
        return ctypes.CFUNCTYPE(result, *arguments)(self.library._handle + offset)

    def decode(self, sample):
        if sample[4:8] != b"NRAW" or sample[1024:1026] != b"\xff\x10":
            raise ValueError("Unexpected NRAW compressed frame")
        payload = ctypes.create_string_buffer(sample[1024:])
        length = len(sample) - 1024
        if self.parse(self.decoder, length, payload, self.input) != 0:
            raise RuntimeError("SDK could not parse this compressed frame")
        if self.configure(self.decoder, self.format, self.options, self.input, self.output) != 0:
            raise RuntimeError("SDK could not configure this frame")
        width, height, depth = self.width(self.output), self.height(self.output), self.depth(self.input)
        if depth != 14 or not 0 < width <= 16384 or not 0 < height <= 12000 or width * height > 100_000_000:
            raise ValueError(f"Unvalidated raster/depth: {width} x {height}, {depth} bits")
        if self.raw is None or self.raw.shape != (height, width):
            self.storage = np.empty(width * height * 2 + 128, dtype=np.uint8)
            offset = (-self.storage.ctypes.data) % 64
            self.raw = self.storage[offset:offset + width * height * 2].view("<u2").reshape(height, width)
        if self.unpack(self.decoder, self.options, self.format, length, payload, self.raw.nbytes, self.raw.ctypes.data, None, None) != 0:
            raise RuntimeError("SDK decompression failed")
        return self.raw, depth
