"""Product images: measurement in code, and the intake port.

Round 16 brings the catalogue's product images into the record.
An image is binary, so it does not travel the fact-batch envelope
the Round 11 contracts judge — it enters through this module's
stated port instead, and the port's discipline is the ingest
port's discipline, applied to bytes:

- **Provenance first (Law 3).** An image presented without a
  source system or an as-of date is refused
  (``missing_provenance``) and nothing is stored — no bytes, no
  fact.
- **Measured, never narrated.** The bytes are measured by code
  at the port: the format is sniffed from the magic bytes (the
  filename is a claim, never the evidence), the dimensions are
  read from the image header, and the content hash is the
  SHA-256 of the bytes as presented. Bytes no measurer here can
  read are refused (``unreadable_image``). The measurers cover
  PNG, JPEG and GIF; other formats are refused until a measurer
  exists — a stated limit, not a silent pass.
- **Stored through the object-store port**, under a
  content-addressed key, and recorded as a ``product_image``
  fact through the ingest port, so the fact carries the
  measurements, the hash and the key, and Law 3 grades the image
  producer like any other. The bytes themselves never enter the
  database — the parity snapshot grades the facts and findings
  byte-identical across dialects while the bytes sit in the
  object store both profiles share by configuration.

The PNG writer here is the generator's fixture press: small
synthetic images built in code (stdlib ``zlib`` + ``struct`` —
no imaging dependency enters the frozen constraint set),
gold-by-construction. A fixture image may carry its subject
class in a PNG ``tEXt`` chunk — the ground truth the offline
vision double reads back, the way a real catalogue feed's asset
metadata would state it. The deterministic checks never read
that chunk; only the vision judgement does.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass

from .ingest import IngestOutcome, IngestPort
from .objectstore import ObjectStore
from .store import SqlStore

IMAGE_KIND = "product_image"
SUBJECT_KEYWORD = "SubjectClass"


class UnreadableImage(RuntimeError):
    """The presented bytes are not an image a measurer here reads."""


@dataclass(frozen=True)
class ImageMeasurement:
    format: str  # sniffed from the bytes: png | jpeg | gif
    width: int
    height: int
    subject_class: str | None  # embedded ground truth, when carried


# -- PNG writing (the fixture press) --------------------------------------


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data)) + tag + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png(width: int, height: int, rgb: tuple[int, int, int],
              subject_class: str | None = None) -> bytes:
    """A small synthetic PNG, built in code: a two-tone checker
    of the stated colour, so different fixtures hash differently
    even at the same size. ``subject_class`` is embedded as a
    ``tEXt`` chunk — the planted ground truth for the vision
    double, never a deterministic-check input."""
    r, g, b = rgb
    alt = (max(0, r - 40), max(0, g - 40), max(0, b - 40))
    rows = []
    for y in range(height):
        row = bytearray(b"\x00")  # filter: none
        for x in range(width):
            row += bytes(alt if (x // 16 + y // 16) % 2 else (r, g, b))
        rows.append(bytes(row))
    parts = [b"\x89PNG\r\n\x1a\n"]
    parts.append(_chunk(b"IHDR", struct.pack(
        ">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
    if subject_class is not None:
        parts.append(_chunk(
            b"tEXt",
            SUBJECT_KEYWORD.encode("latin-1") + b"\x00"
            + subject_class.encode("latin-1"),
        ))
    parts.append(_chunk(b"IDAT", zlib.compress(b"".join(rows), 9)))
    parts.append(_chunk(b"IEND", b""))
    return b"".join(parts)


# -- measurement ------------------------------------------------------------


def _measure_png(data: bytes) -> ImageMeasurement:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise UnreadableImage("png without an IHDR chunk")
    width, height = struct.unpack(">II", data[16:24])
    subject = None
    offset = 8
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset:offset + 4])
        tag = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + length]
        if tag == b"tEXt" and b"\x00" in body:
            keyword, _, value = body.partition(b"\x00")
            if keyword.decode("latin-1") == SUBJECT_KEYWORD:
                subject = value.decode("latin-1")
        offset += 12 + length
        if tag == b"IEND":
            break
    return ImageMeasurement("png", width, height, subject)


def _measure_jpeg(data: bytes) -> ImageMeasurement:
    offset = 2
    while offset + 9 < len(data):
        if data[offset] != 0xFF:
            raise UnreadableImage("jpeg marker stream is misaligned")
        marker = data[offset + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[offset + 5:offset + 9])
            return ImageMeasurement("jpeg", width, height, None)
        (segment_length,) = struct.unpack(">H", data[offset + 2:offset + 4])
        offset += 2 + segment_length
    raise UnreadableImage("jpeg without a frame header")


def _measure_gif(data: bytes) -> ImageMeasurement:
    if len(data) < 10:
        raise UnreadableImage("gif too short for its header")
    width, height = struct.unpack("<HH", data[6:10])
    return ImageMeasurement("gif", width, height, None)


def measure_image(data: bytes) -> ImageMeasurement:
    """Sniff and measure image bytes. Raises
    :class:`UnreadableImage` for anything the measurers here do
    not read — including formats (WebP and friends) that have no
    measurer yet."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _measure_png(data)
    if data[:2] == b"\xff\xd8":
        return _measure_jpeg(data)
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return _measure_gif(data)
    raise UnreadableImage("no measurer for these bytes")


def embedded_subject(data: bytes) -> str | None:
    """The subject class embedded in the bytes, when the format
    carries one — the vision double's ground-truth read. Returns
    None for unreadable bytes rather than raising: a judgement
    over bytes nobody can read is an unjudged image, which the
    listing pack names as such."""
    try:
        return measure_image(data).subject_class
    except UnreadableImage:
        return None


_EXTENSION_FORMATS = {
    ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg",
    ".gif": "gif", ".webp": "webp",
}


def declared_format(filename: str) -> str:
    """The format a filename claims — a claim, judged against
    the sniffed format by the listing pack, never trusted."""
    lowered = filename.lower()
    for extension, fmt in _EXTENSION_FORMATS.items():
        if lowered.endswith(extension):
            return fmt
    return "unknown"


def object_key_for(content_hash: str) -> str:
    return f"product-images/{content_hash}"


# -- the intake port --------------------------------------------------------


class ProductImageIntake:
    """The stated port for product images: provenance, measure,
    store the bytes, record the fact — or refuse, with the
    reason named and nothing stored."""

    def __init__(self, store: SqlStore, ingest: IngestPort,
                 objects: ObjectStore):
        self._store = store
        self._ingest = ingest
        self._objects = objects

    def ingest_image(self, *, sku: str, filename: str, data: bytes,
                     declared_class: str, source_system: str | None,
                     as_of: str | None) -> IngestOutcome:
        if not source_system or not str(source_system).strip() \
                or not as_of or not str(as_of).strip():
            return IngestOutcome(
                status="refused", reason="missing_provenance")
        try:
            measured = measure_image(data)
        except UnreadableImage:
            return IngestOutcome(
                status="refused", reason="unreadable_image")
        content_hash = hashlib.sha256(data).hexdigest()
        key = object_key_for(content_hash)
        stored_hash = self._objects.put(key, data)
        if stored_hash != content_hash:
            return IngestOutcome(
                status="refused", reason="object_store_hash_mismatch")
        payload = {
            "sku": sku,
            "filename": filename,
            "declared_format": declared_format(filename),
            "format": measured.format,
            "width": measured.width,
            "height": measured.height,
            "byte_count": len(data),
            "content_hash": content_hash,
            "object_key": key,
            "declared_class": declared_class,
        }
        return self._ingest.ingest(
            kind=IMAGE_KIND,
            natural_key=f"image:{sku}:{content_hash[:20]}",
            payload=payload,
            source_system=source_system,
            as_of=as_of,
        )
