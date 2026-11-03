from __future__ import annotations

import hashlib
import inspect
import random
import struct
import zlib
from io import BytesIO
from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Text
from pypdf.generic import (
    ArrayObject,
    ByteStringObject,
    DecodedStreamObject,
    DictionaryObject,
    EncodedStreamObject,
    IndirectObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from adsfolio.pdf import annotation_revision
from adsfolio.pdf.annotation_revision import annotation_only_revision

_INCREMENTAL_WRITER = "incremental" in inspect.signature(PdfWriter).parameters


def _write(writer: PdfWriter) -> bytes:
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _base_pdf(*, info: bool = True, identifier: bool = False) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=400)
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf (Original text) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    font = DictionaryObject({NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    metadata = DecodedStreamObject()
    metadata.set_data(b"<metadata><doi>10.1000/original</doi></metadata>")
    metadata[NameObject("/Type")] = NameObject("/Metadata")
    metadata[NameObject("/Subtype")] = NameObject("/XML")
    writer._root_object[NameObject("/Metadata")] = writer._add_object(metadata)
    fake_page = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Page"),
            NameObject("/Annots"): ArrayObject([TextStringObject("original")]),
        }
    )
    writer._root_object[NameObject("/FakePage")] = writer._add_object(fake_page)
    if info:
        writer.add_metadata(
            {
                "/Title": "Original paper",
                "/Subject": "doi:10.1000/original",
                "/ModDate": "D:20260101000000Z",
            }
        )
    elif _INCREMENTAL_WRITER:
        writer.metadata = None
    if identifier:
        writer._ID = ArrayObject(
            [
                ByteStringObject(b"first identifier"),
                ByteStringObject(b"second identifier"),
            ]
        )
    result = _write(writer)
    if not info and not _INCREMENTAL_WRITER:
        reader = PdfReader(BytesIO(result))
        info_ref = reader.trailer.raw_get("/Info")
        info_entry = f"/Info {info_ref.idnum} {info_ref.generation} R".encode()
        assert result.count(info_entry) == 1
        result = result.replace(info_entry, b" " * len(info_entry))
    return result


def _serialize(value) -> bytes:
    stream = BytesIO()
    value.write_to_stream(stream)
    return stream.getvalue()


class _IncrementalWriterFixture:
    """Write test revisions when pypdf lacks an incremental writer."""

    def __init__(self, old: bytes):
        self.old = old
        self.reader = PdfReader(BytesIO(old), strict=True)
        self.pages = self.reader.pages
        self._root_object = self.reader.trailer["/Root"]
        self._ID = self.reader.trailer.get("/ID")
        self._info_ref = self.reader.trailer.get("/Info")
        self._info = self._info_ref.get_object() if self._info_ref is not None else None
        self.objects = {}
        for generation, entries in self.reader.xref.items():
            for obj_id in entries:
                if obj_id == 0 or self.reader.xref_free_entry.get(generation, {}).get(
                    obj_id, False
                ):
                    continue
                reference = IndirectObject(obj_id, generation, self.reader)
                self.objects[(obj_id, generation)] = reference.get_object()
        for page in self.pages:
            reference = page.indirect_reference
            self.objects[(reference.idnum, reference.generation)] = page
        self.before = {key: _serialize(value) for key, value in self.objects.items()}

    def add_annotation(self, page_number, annotation):
        page = self.pages[page_number]
        annotations = page.get("/Annots")
        if annotations is None:
            annotations = ArrayObject()
            page[NameObject("/Annots")] = annotations
        annotations.append(annotation)

    def add_metadata(self, values):
        if self._info is None:
            next_id = max((key[0] for key in self.objects), default=0) + 1
            self._info = DictionaryObject()
            self.objects[(next_id, 0)] = self._info
            self._info_ref = IndirectObject(next_id, 0, self.reader)
        for key, value in values.items():
            self._info[NameObject(key)] = TextStringObject(value)

    def _add_object(self, value):
        next_id = max((key[0] for key in self.objects), default=0) + 1
        self.objects[(next_id, 0)] = value
        return IndirectObject(next_id, 0, self.reader)

    def write(self, output):
        output.write(self.old)
        offsets = {}
        for (obj_id, generation), value in self.objects.items():
            data = _serialize(value)
            if self.before.get((obj_id, generation)) == data:
                continue
            offsets[(obj_id, generation)] = output.tell()
            output.write(f"{obj_id} {generation} obj\n".encode())
            output.write(data)
            output.write(b"\nendobj\n")
        xref = output.tell()
        output.write(b"xref\n")
        if not offsets:
            output.write(b"0 1\n0000000000 65535 f \n")
        for (obj_id, generation), offset in sorted(offsets.items()):
            output.write(f"{obj_id} 1\n{offset:010d} {generation:05d} n \n".encode())
        size = max((key[0] for key in self.objects), default=0) + 1
        previous = int(self.old.rsplit(b"startxref", 1)[1].split()[0])
        trailer = DictionaryObject(
            {
                NameObject("/Size"): NumberObject(size),
                NameObject("/Root"): self.reader.trailer.raw_get("/Root"),
                NameObject("/Prev"): NumberObject(previous),
            }
        )
        if self._info_ref is not None:
            trailer[NameObject("/Info")] = self._info_ref
        if self._ID is not None:
            trailer[NameObject("/ID")] = self._ID
        output.write(b"trailer\n")
        output.write(_serialize(trailer))
        output.write(f"\nstartxref\n{xref}\n%%EOF\n".encode())


def _revision(old: bytes, change=None, *, annotation: bool = True) -> bytes:
    writer = (
        PdfWriter(BytesIO(old), incremental=True)
        if _INCREMENTAL_WRITER
        else _IncrementalWriterFixture(old)
    )
    if annotation:
        writer.add_annotation(0, Text(rect=(10, 10, 20, 20), text="A note"))
    if change is not None:
        change(writer)
    result = _write(writer)
    assert result.startswith(old)
    return result


def _compare(old: bytes, new: bytes) -> bool:
    return annotation_only_revision(new, len(old), hashlib.sha256(old).hexdigest())


def _linearized_xref_pdf() -> bytes:
    header = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
    early = (
        b"xref\n0 1\n0000000000 65535 f \n"
        b"trailer\n<< /Size 5 /Root 3 0 R /Info 1 0 R /Prev 0000000000 >>\n"
    )
    objects = [
        b"1 0 obj\n<< /Title (Example) >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [4 0 R] >>\nendobj\n",
        b"3 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Resources << >> >>\nendobj\n",
    ]
    offsets = []
    position = len(header) + len(early)
    for item in objects:
        offsets.append(position)
        position += len(item)
    early = early.replace(b"/Prev 0000000000", f"/Prev {position:010d}".encode())
    entries = b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    late = (
        b"xref\n1 4\n" + entries + b"trailer\n<< /Size 5 /Root 3 0 R /Info 1 0 R >>\n"
    )
    return (
        header
        + early
        + b"".join(objects)
        + late
        + f"startxref\n{len(header)}\n%%EOF\n".encode()
    )


def _classic_xref_revision(old: bytes, ranges: list[tuple[int, int]]) -> bytes:
    reader = PdfReader(BytesIO(old), strict=True)
    page = reader.pages[0]
    page_ref = page.indirect_reference
    page[NameObject("/Annots")] = ArrayObject(
        [Text(rect=(10, 10, 20, 20), text="A note")]
    )
    page_object = (
        f"{page_ref.idnum} {page_ref.generation} obj\n".encode()
        + _serialize(page)
        + b"\nendobj\n"
    )
    offsets = dict(reader.xref[0])
    offsets[page_ref.idnum] = len(old)
    xref_offset = len(old) + len(page_object)
    sections = bytearray(b"xref\n")
    for first, count in ranges:
        sections.extend(f"{first} {count}\n".encode())
        for obj_id in range(first, first + count):
            sections.extend(f"{offsets[obj_id]:010d} 00000 n \n".encode())
    root_ref = reader.trailer.raw_get("/Root")
    info_ref = reader.trailer.raw_get("/Info")
    previous = int(old.rsplit(b"startxref", 1)[1].split()[0])
    trailer = (
        f"trailer\n<< /Size {reader.trailer['/Size']} "
        f"/Root {root_ref.idnum} {root_ref.generation} R "
        f"/Info {info_ref.idnum} {info_ref.generation} R /Prev {previous} >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    return old + page_object + bytes(sections) + trailer


def _xref_stream_revision(
    old: bytes, ranges: list[tuple[int, int]] | None = None
) -> bytes:
    reader = PdfReader(BytesIO(old), strict=True)
    page = reader.pages[0]
    page_ref = page.indirect_reference
    page[NameObject("/Annots")] = ArrayObject(
        [Text(rect=(10, 10, 20, 20), text="A note")]
    )
    page_object = (
        f"{page_ref.idnum} {page_ref.generation} obj\n".encode()
        + _serialize(page)
        + b"\nendobj\n"
    )
    xref_id = max(obj_id for entries in reader.xref.values() for obj_id in entries) + 1
    xref_offset = len(old) + len(page_object)
    if ranges is None:
        ranges = [(page_ref.idnum, 1), (xref_id, 1)]
    offsets = dict(reader.xref[0])
    offsets[page_ref.idnum] = len(old)
    offsets[xref_id] = xref_offset
    entries = zlib.compress(
        b"".join(
            (
                struct.pack(">BIH", 0, 0, 65535)
                if obj_id == 0
                else struct.pack(">BIH", 1, offsets[obj_id], 0)
            )
            for first, count in ranges
            for obj_id in range(first, first + count)
        )
    )
    root_ref = reader.trailer.raw_get("/Root")
    info_ref = reader.trailer.raw_get("/Info")
    previous = int(old.rsplit(b"startxref", 1)[1].split()[0])
    dictionary = (
        f"<< /Type /XRef /Size {xref_id + 1} /Root {root_ref.idnum} {root_ref.generation} R "
        f"/Info {info_ref.idnum} {info_ref.generation} R /Prev {previous} "
        f"/W [1 4 2] /Index [{' '.join(f'{first} {count}' for first, count in ranges)}] "
        f"/Filter /FlateDecode /Length {len(entries)} >>"
    ).encode()
    return (
        old
        + page_object
        + f"{xref_id} 0 obj\n".encode()
        + dictionary
        + b"\nstream\n"
        + entries
        + b"\nendstream\nendobj\n"
        + f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )


def test_accepts_incremental_annotation_with_unchanged_document():
    old = _base_pdf()
    assert _compare(old, _revision(old))


def test_accepts_revision_without_reader_startxref_attribute(monkeypatch):
    old = _base_pdf()
    new = _revision(old)
    initialize = PdfReader.__init__

    def without_startxref(reader, *args, **kwargs):
        initialize(reader, *args, **kwargs)
        if hasattr(reader, "_startxref"):
            del reader._startxref

    monkeypatch.setattr(PdfReader, "__init__", without_startxref)
    assert _compare(old, new)


def test_accepts_incremental_cross_reference_stream():
    old = _base_pdf()
    assert _compare(old, _xref_stream_revision(old))


def test_accepts_xref_stream_with_default_index():
    old = _base_pdf()
    reader = PdfReader(BytesIO(old), strict=True)
    xref_id = max(obj_id for entries in reader.xref.values() for obj_id in entries) + 1
    new = _xref_stream_revision(old, [(0, xref_id + 1)])
    index = f"/Index [0 {xref_id + 1}] ".encode()
    assert new.count(index) == 1
    assert _compare(old, new.replace(index, b""))


@pytest.mark.parametrize("kind", ["classic", "stream"])
@pytest.mark.parametrize("overlap", ["duplicate", "partial"])
def test_rejects_overlapping_xref_ranges(kind, overlap):
    old = _base_pdf()
    reader = PdfReader(BytesIO(old), strict=True)
    page_id = reader.pages[0].indirect_reference.idnum
    xref_id = max(obj_id for entries in reader.xref.values() for obj_id in entries) + 1
    if overlap == "duplicate":
        ranges = [(page_id, 1), (page_id, 1)]
    else:
        ranges = [(page_id, 2), (page_id + 1, 1)]
    if kind == "stream":
        ranges.append((xref_id, 1))
        new = _xref_stream_revision(old, ranges)
    else:
        new = _classic_xref_revision(old, ranges)
    assert not _compare(old, new)


@pytest.mark.parametrize("kind", ["classic", "stream"])
def test_accepts_adjacent_xref_ranges(kind):
    old = _base_pdf()
    reader = PdfReader(BytesIO(old), strict=True)
    page_id = reader.pages[0].indirect_reference.idnum
    xref_id = max(obj_id for entries in reader.xref.values() for obj_id in entries) + 1
    ranges = [(page_id, 1), (page_id + 1, 1)]
    if kind == "stream":
        ranges.append((xref_id, 1))
        new = _xref_stream_revision(old, ranges)
    else:
        new = _classic_xref_revision(old, ranges)
    assert _compare(old, new)


@pytest.mark.parametrize("kind", ["classic", "stream"])
def test_rejects_overlaps_in_an_intermediate_revision(kind):
    old = _base_pdf()
    reader = PdfReader(BytesIO(old), strict=True)
    page_id = reader.pages[0].indirect_reference.idnum
    xref_id = max(obj_id for entries in reader.xref.values() for obj_id in entries) + 1
    ranges = [(page_id, 1), (page_id, 1)]
    if kind == "stream":
        ranges.append((xref_id, 1))
        intermediate = _xref_stream_revision(old, ranges)
    else:
        intermediate = _classic_xref_revision(old, ranges)
    assert not _compare(old, _revision(intermediate))


@pytest.mark.parametrize("index", [b"[1]", b"[1 -1]", b"[1 1.5]"])
def test_rejects_malformed_xref_stream_index(index):
    old = _base_pdf()
    new = _xref_stream_revision(old)
    start = new.index(b"/Index [", len(old))
    end = new.index(b"]", start) + 1
    malformed = new[:start] + b"/Index " + index + new[end:]
    assert not _compare(old, malformed)


def test_rejects_cyclic_xref_before_constructing_reader(monkeypatch):
    old = _linearized_xref_pdf()
    first = int(old.rsplit(b"startxref", 1)[1].split()[0])
    previous = old.split(b"/Prev ", 1)[1].split(None, 1)[0]
    cyclic = old.replace(b"/Prev " + previous, f"/Prev {first:010d}".encode(), 1)
    calls = []
    initialize = PdfReader.__init__

    def track_reader(reader, *args, **kwargs):
        calls.append(True)
        initialize(reader, *args, **kwargs)

    monkeypatch.setattr(PdfReader, "__init__", track_reader)
    assert not _compare(cyclic, cyclic + b"\n")
    assert not calls


def test_accepts_incremental_annotation_with_changed_modification_date():
    old = _base_pdf()
    new = _revision(
        old, lambda writer: writer.add_metadata({"/ModDate": "D:20261103000000Z"})
    )
    assert _compare(old, new)


def test_accepts_annotation_without_info_in_either_revision():
    old = _base_pdf(info=False)
    assert _compare(old, _revision(old))


def test_accepts_multiple_incremental_annotation_revisions():
    old = _base_pdf()
    first = _revision(old)
    second = _revision(first)
    assert _compare(old, second)


def test_accepts_forward_previous_xref_in_linearized_style_pdf():
    old = _linearized_xref_pdf()
    assert len(PdfReader(BytesIO(old), strict=True).pages) == 1
    assert _compare(old, _revision(old))


def test_accepts_unchanged_trailer_identifier():
    old = _base_pdf(identifier=True)
    assert _compare(old, _revision(old))


def test_rejects_changed_page_stream():
    old = _base_pdf()

    def change(writer):
        writer.pages[0]["/Contents"].set_data(b"BT (Changed text) Tj ET")

    assert not _compare(old, _revision(old, change))


def test_rejects_changed_page_resources():
    old = _base_pdf()

    def change(writer):
        writer.pages[0]["/Resources"]["/Font"]["/F1"][NameObject("/BaseFont")] = (
            NameObject("/Courier")
        )

    assert not _compare(old, _revision(old, change))


def test_rejects_changed_doi_in_info():
    old = _base_pdf()
    new = _revision(
        old, lambda writer: writer.add_metadata({"/Subject": "doi:10.1000/changed"})
    )
    assert not _compare(old, new)


def test_rejects_changed_xmp_metadata():
    old = _base_pdf()

    def change(writer):
        writer._root_object["/Metadata"].set_data(
            b"<metadata><doi>10.1000/changed</doi></metadata>"
        )

    assert not _compare(old, _revision(old, change))


def test_rejects_annots_change_in_dictionary_outside_page_tree():
    old = _base_pdf()

    def change(writer):
        fake = writer._root_object["/FakePage"]
        fake[NameObject("/Annots")] = ArrayObject([TextStringObject("changed")])

    assert not _compare(old, _revision(old, change))


def test_accepts_changed_revision_identifier():
    old = _base_pdf(identifier=True)

    def change(writer):
        writer._ID[1] = ByteStringObject(b"changed identifier")

    assert _compare(old, _revision(old, change))


def test_rejects_changed_permanent_file_identifier():
    old = _base_pdf(identifier=True)

    def change(writer):
        writer._ID[0] = ByteStringObject(b"changed identifier")

    assert not _compare(old, _revision(old, change))


def test_rejects_malformed_file_identifier():
    old = _base_pdf(identifier=True)

    def change(writer):
        writer._ID = ArrayObject([ByteStringObject(b"only one identifier")])

    assert not _compare(old, _revision(old, change))


def test_rejects_new_info_dictionary():
    old = _base_pdf(info=False)
    new = _revision(old, lambda writer: writer.add_metadata({"/Title": "New identity"}))
    assert not _compare(old, new)


@pytest.mark.parametrize("size", [-1, 0, 1, 10**6, True])
def test_rejects_invalid_previous_size(size):
    old = _base_pdf()
    new = _revision(old)
    assert not annotation_only_revision(new, size, hashlib.sha256(old).hexdigest())


def test_rejects_non_strict_prefix_and_wrong_hash():
    old = _base_pdf()
    new = _revision(old)
    digest = hashlib.sha256(old).hexdigest()
    assert not annotation_only_revision(old, len(old), digest)
    assert not annotation_only_revision(new, len(old), "0" * 64)
    assert not annotation_only_revision(new, len(old), "not-a-sha256")
    assert annotation_only_revision(new, len(old), digest.upper())


def test_rejects_appended_bytes_without_a_revision():
    old = _base_pdf()
    assert not _compare(old, old + b"\n% trailing comment\n")


def test_rejects_malformed_prefix_and_malformed_current_pdf():
    old = _base_pdf()
    new = _revision(old)
    bad_old = b"%PDF-1.7\nnot a PDF\n%%EOF\n"
    assert not _compare(bad_old, bad_old + b"more")
    assert not _compare(old, new[:-20])


def test_rejects_no_pages():
    writer = PdfWriter()
    old = _write(writer)
    new = _revision(old, annotation=False)
    assert not _compare(old, new)


def test_rejects_encrypted_document():
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.encrypt("password")
    old = _write(writer)
    assert not _compare(old, old + b"\n% more bytes\n")


def test_rejects_broken_page_count():
    old = _base_pdf()

    def change(writer):
        writer._root_object["/Pages"][NameObject("/Count")] = NumberObject(2)

    assert not _compare(old, _revision(old, change))


def test_rejects_page_type_that_is_a_string_instead_of_a_name():
    writer = PdfWriter()
    page = writer.add_blank_page(300, 400)
    page[NameObject("/Type")] = TextStringObject("/Page")
    old = _write(writer)
    assert not _compare(old, _revision(old))


def test_rejects_changed_numeric_value_beyond_float_precision():
    first = b"0.12345678901234567890123456789"
    second = b"0.12345678901234567890123456788"
    writer = PdfWriter()
    page = writer.add_blank_page(300, 400)
    placeholder = b"(" + b"0" * (len(first) - 2) + b")"
    page[NameObject("/PrecisionCheck")] = TextStringObject("0" * (len(first) - 2))
    original = _write(writer)
    assert placeholder in original
    old = original.replace(placeholder, first)
    reader = PdfReader(BytesIO(old), strict=True)
    page_ref = reader.pages[0].indirect_reference
    offset = reader.xref[page_ref.generation][page_ref.idnum]
    page_end = old.index(b"endobj", offset) + len(b"endobj")
    updated_page = old[offset:page_end].replace(first, second)
    new_xref = len(old) + len(updated_page) + 1
    previous = int(old.rsplit(b"startxref", 1)[1].split()[0])
    root_ref = reader.trailer.raw_get("/Root")
    info_ref = reader.trailer.raw_get("/Info")
    revision = (
        updated_page
        + b"\n"
        + f"xref\n{page_ref.idnum} 1\n{len(old):010d} 00000 n \n".encode()
        + (
            f"trailer\n<< /Size 5 /Root {root_ref.idnum} {root_ref.generation} R "
            f"/Info {info_ref.idnum} {info_ref.generation} R /Prev {previous} >>\n"
        ).encode()
        + f"startxref\n{new_xref}\n%%EOF\n".encode()
    )
    new = old + revision
    assert (
        PdfReader(BytesIO(old)).pages[0]["/PrecisionCheck"]
        == PdfReader(BytesIO(new)).pages[0]["/PrecisionCheck"]
    )
    assert not _compare(old, new)


def test_compares_opaque_image_streams_without_decoding(monkeypatch):
    writer = PdfWriter()
    writer.add_blank_page(300, 400)
    image = EncodedStreamObject()
    image._data = b"opaque image bytes"
    image[NameObject("/Filter")] = NameObject("/CCITTFaxDecode")
    writer._root_object[NameObject("/Image")] = writer._add_object(image)
    old = _write(writer)
    new = _revision(old)

    def change(writer):
        writer._root_object["/Image"]._data = b"changed image bytes"

    changed = _revision(old, change)
    original_get_data = EncodedStreamObject.get_data

    def get_data(stream):
        if stream.get("/Filter") == "/CCITTFaxDecode":
            raise TypeError("Decoder cannot handle this stream")
        return original_get_data(stream)

    monkeypatch.setattr(EncodedStreamObject, "get_data", get_data)
    assert _compare(old, new)
    assert not _compare(old, changed)


@pytest.fixture(scope="module")
def large_flate_stream():
    random_bytes = random.Random(12345).randbytes(2 * 1024 * 1024)
    return zlib.compress(random_bytes + b"A" * (4 * 1024 * 1024))


def _pdf_with_flate_stream(data, filter_name="/FlateDecode"):
    writer = PdfWriter()
    writer.add_blank_page(300, 400)
    stream = EncodedStreamObject()
    stream._data = data
    stream[NameObject("/Filter")] = NameObject(filter_name)
    writer._root_object[NameObject("/Metadata")] = writer._add_object(stream)
    return _write(writer)


@pytest.mark.parametrize("filter_name", ["/FlateDecode", "/Fl"])
def test_accepts_large_flate_with_bounded_decompression(
    large_flate_stream, filter_name, monkeypatch
):
    old = _pdf_with_flate_stream(large_flate_stream, filter_name)
    new = _revision(old)
    make_decoder = zlib.decompressobj
    calls = []

    class TrackingDecoder:
        def __init__(self, *args, **kwargs):
            self.decoder = make_decoder(*args, **kwargs)

        def decompress(self, data, max_length=0):
            result = self.decoder.decompress(data, max_length)
            calls.append(
                (len(data), max_length, len(result), len(self.decoder.unconsumed_tail))
            )
            return result

        def __getattr__(self, name):
            return getattr(self.decoder, name)

    monkeypatch.setattr(
        annotation_revision, "zlib", SimpleNamespace(decompressobj=TrackingDecoder)
    )
    assert _compare(old, new)
    assert calls
    assert max(call[0] for call in calls) <= 256 * 1024
    assert all(0 < call[1] <= 65536 and call[2] <= 65536 for call in calls)
    assert any(call[3] for call in calls)


@pytest.mark.parametrize(
    "variant",
    [
        "truncated_body",
        "missing_checksum",
        "bad_checksum",
        "bad_header",
        "extra",
        "concatenated",
    ],
)
def test_rejects_invalid_large_flate_stream(large_flate_stream, variant):
    if variant == "truncated_body":
        data = large_flate_stream[: len(large_flate_stream) // 2]
    elif variant == "missing_checksum":
        data = large_flate_stream[:-1]
    elif variant == "bad_checksum":
        data = large_flate_stream[:-1] + bytes([large_flate_stream[-1] ^ 1])
    elif variant == "bad_header":
        data = b"\x00" + large_flate_stream[1:]
    elif variant == "extra":
        data = large_flate_stream + b"trailing data"
    else:
        data = large_flate_stream + zlib.compress(b"another stream")
    old = _pdf_with_flate_stream(data)
    assert not _compare(old, _revision(old))


@pytest.mark.parametrize("force_fallback", [False, True], ids=["current", "fallback"])
@pytest.mark.parametrize("valid", [True, False], ids=["valid", "invalid"])
def test_validates_large_flate_in_new_annotation(
    large_flate_stream, valid, force_fallback, monkeypatch
):
    if force_fallback:
        monkeypatch.setitem(globals(), "_INCREMENTAL_WRITER", False)
    old = _base_pdf()

    def change(writer):
        annotation = writer.pages[0]["/Annots"][-1].get_object()
        stream = EncodedStreamObject()
        stream._data = large_flate_stream if valid else large_flate_stream + b"extra"
        stream[NameObject("/Filter")] = NameObject("/FlateDecode")
        appearance = writer._add_object(stream)
        annotation[NameObject("/AP")] = DictionaryObject({NameObject("/N"): appearance})

    assert _compare(old, _revision(old, change)) is valid


@pytest.mark.parametrize("size", [0, 1, 65535, 65536, 65537, 131072])
def test_accepts_flate_output_at_decompression_limit(size):
    old = _pdf_with_flate_stream(zlib.compress(b"A" * size))
    assert _compare(old, _revision(old))


@pytest.mark.parametrize("before_boundary", [0, 1], ids=["next-chunk", "same-chunk"])
def test_rejects_extra_flate_data_at_chunk_boundary(before_boundary):
    target_length = 2 * 65536 - before_boundary
    for length in range(target_length - 64, target_length + 1):
        data = zlib.compress(b"A" * length, level=0)
        if len(data) == target_length:
            break
    else:
        pytest.fail("Could not construct a stream at the target boundary")
    valid = _pdf_with_flate_stream(data)
    assert _compare(valid, _revision(valid))
    old = _pdf_with_flate_stream(data + b"X")
    assert not _compare(old, _revision(old))


def test_rejects_malformed_compressed_stream():
    writer = PdfWriter()
    writer.add_blank_page(300, 400)
    stream = EncodedStreamObject()
    stream._data = b"not valid zlib data"
    stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    writer._root_object[NameObject("/Metadata")] = writer._add_object(stream)
    old = _write(writer)
    assert not _compare(old, _revision(old))


def test_rejects_malformed_page_annotations():
    old = _base_pdf()

    def change(writer):
        writer.pages[0][NameObject("/Annots")] = TextStringObject("not an array")

    assert not _compare(old, _revision(old, change, annotation=False))


def test_accepts_pymupdf_incremental_annotation(tmp_path):
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "synthetic.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Original text")
        document.set_metadata(
            {"title": "Original paper", "subject": "doi:10.1000/original"}
        )
        document.save(path)
    old = path.read_bytes()
    with fitz.open(path) as document:
        document[0].add_text_annot((100, 100), "A note")
        document.save(
            path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP, no_new_id=True
        )
    new = path.read_bytes()
    assert new.startswith(old)
    assert _compare(old, new)


def test_accepts_incremental_annotation_with_object_streams(tmp_path):
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "object-streams.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Original text")
        document.save(path, use_objstms=1)
    old = path.read_bytes()
    assert PdfReader(BytesIO(old), strict=True).xref_objStm
    with fitz.open(path) as document:
        document[0].add_text_annot((100, 100), "A note")
        document.saveIncr()
    assert _compare(old, path.read_bytes())


def test_accepts_pymupdf_annotation_of_pypdf_document(tmp_path):
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "synthetic.pdf"
    old = _base_pdf()
    path.write_bytes(old)
    with fitz.open(path) as document:
        document[0].add_text_annot((50, 50), "A note")
        document.save(
            path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP, no_new_id=True
        )
    assert _compare(old, path.read_bytes())


def test_accepts_file_identifier_added_by_pymupdf(tmp_path):
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "synthetic.pdf"
    old = _base_pdf()
    path.write_bytes(old)
    with fitz.open(path) as document:
        document[0].add_text_annot((50, 50), "A note")
        document.saveIncr()
    new = path.read_bytes()
    assert "/ID" not in PdfReader(BytesIO(old)).trailer
    assert "/ID" in PdfReader(BytesIO(new)).trailer
    assert _compare(old, new)


def test_accepts_revision_identifier_changed_by_pymupdf(tmp_path):
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "synthetic.pdf"
    old = _base_pdf(identifier=True)
    path.write_bytes(old)
    with fitz.open(path) as document:
        document[0].add_text_annot((50, 50), "A note")
        document.saveIncr()
    new = path.read_bytes()
    old_id = PdfReader(BytesIO(old)).trailer["/ID"]
    new_id = PdfReader(BytesIO(new)).trailer["/ID"]
    assert old_id[0] == new_id[0]
    assert old_id[1] != new_id[1]
    assert _compare(old, new)
