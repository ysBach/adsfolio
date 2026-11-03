"""Compare an incremental PDF revision without changing identity proof.

The comparison accepts only changes to page annotations and `/Info/ModDate`.
It does not establish which publication a PDF contains.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import zlib
from decimal import Decimal
from io import BytesIO
from typing import Any

from pypdf import PdfReader
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    ByteStringObject,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NameObject,
    NullObject,
    NumberObject,
    StreamObject,
    TextStringObject,
    read_object,
)

_WHITESPACE = b"\x00\t\n\x0c\r "
_DELIMITERS = b"()<>[]{}/%"
_NUMBER = re.compile(rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_INTEGER = re.compile(rb"\d+\Z")
_HEADER = re.compile(
    rb"(\d+)[\x00\t\n\x0c\r ]+(\d+)[\x00\t\n\x0c\r ]+obj(?=[\x00\t\n\x0c\r ()<>\[\]{}/%])"
)
_HASH = re.compile(r"[0-9a-fA-F]{64}\Z")
_FLATE_CHUNK_SIZE = 65536
_TRAILER_STRUCTURE = {b"Size", b"Prev", b"XRefStm"}
_XREF_STREAM_STRUCTURE = {
    b"Type",
    b"W",
    b"Index",
    b"Length",
    b"Filter",
    b"DecodeParms",
    b"DL",
}


class _InvalidPdf(ValueError):
    """Mark a PDF structure that cannot be compared safely."""


class _Lexer:
    """Retain exact PDF numbers and reject ambiguous dictionary syntax."""

    def __init__(self, data: bytes, position: int = 0):
        self.data = data
        self.position = position

    def skip(self) -> None:
        data = self.data
        while self.position < len(data):
            c = data[self.position]
            if c in _WHITESPACE:
                self.position += 1
            elif c == ord("%"):
                self.position += 1
                while self.position < len(data) and data[self.position] not in b"\r\n":
                    self.position += 1
            else:
                break

    def token(self) -> tuple[str, bytes]:
        self.skip()
        data = self.data
        start = self.position
        if start == len(data):
            return ("end", b"")
        if data[start : start + 2] in (b"<<", b">>"):
            self.position += 2
            return ("delimiter", data[start : self.position])
        c = data[start]
        if c in b"[]":
            self.position += 1
            return ("delimiter", data[start : self.position])
        if c == ord("/"):
            self.position += 1
            name = bytearray()
            while self.position < len(data):
                c = data[self.position]
                if c in _WHITESPACE or c in _DELIMITERS:
                    break
                if c == ord("#"):
                    encoded = data[self.position + 1 : self.position + 3]
                    if len(encoded) != 2 or not re.fullmatch(
                        rb"[0-9a-fA-F]{2}", encoded
                    ):
                        raise _InvalidPdf("Invalid name escape")
                    name.append(int(encoded, 16))
                    self.position += 3
                else:
                    name.append(c)
                    self.position += 1
            return ("name", bytes(name))
        if c == ord("("):
            self.position += 1
            depth = 1
            while self.position < len(data) and depth:
                c = data[self.position]
                self.position += 1
                if c == ord("\\"):
                    if self.position >= len(data):
                        raise _InvalidPdf("Incomplete string escape")
                    if data[self.position : self.position + 2] == b"\r\n":
                        self.position += 2
                    else:
                        self.position += 1
                elif c == ord("("):
                    depth += 1
                elif c == ord(")"):
                    depth -= 1
            if depth:
                raise _InvalidPdf("Unterminated string")
            return ("string", data[start : self.position])
        if c == ord("<"):
            self.position += 1
            while self.position < len(data) and data[self.position] != ord(">"):
                if data[self.position] not in _WHITESPACE + b"0123456789abcdefABCDEF":
                    raise _InvalidPdf("Invalid hexadecimal string")
                self.position += 1
            if self.position == len(data):
                raise _InvalidPdf("Unterminated hexadecimal string")
            self.position += 1
            return ("string", data[start : self.position])
        if c in _DELIMITERS:
            raise _InvalidPdf("Unexpected delimiter")
        while self.position < len(data):
            c = data[self.position]
            if c in _WHITESPACE or c in _DELIMITERS:
                break
            self.position += 1
        word = data[start : self.position]
        return ("number" if _NUMBER.fullmatch(word) else "word", word)

    def value(self) -> tuple[Any, ...]:
        kind, token = self.token()
        if token == b"<<":
            entries: dict[bytes, tuple[Any, ...]] = {}
            while True:
                kind, key = self.token()
                if key == b">>":
                    return ("dict", tuple(sorted(entries.items())))
                if kind != "name" or key in entries:
                    raise _InvalidPdf("Invalid or duplicate dictionary key")
                entries[key] = self.value()
        if token == b"[":
            entries = []
            while True:
                position = self.position
                _, item = self.token()
                if item == b"]":
                    return ("array", tuple(entries))
                if not item:
                    raise _InvalidPdf("Unterminated array")
                self.position = position
                entries.append(self.value())
        if kind == "number":
            position = self.position
            next_kind, next_token = self.token()
            if (
                _INTEGER.fullmatch(token)
                and next_kind == "number"
                and _INTEGER.fullmatch(next_token)
            ):
                _, last = self.token()
                if last == b"R":
                    return ("ref", int(token), int(next_token))
            self.position = position
            return ("number", Decimal(token.decode("ascii")))
        if kind in ("name", "string"):
            return (kind, token)
        if kind == "word" and token in (b"true", b"false", b"null"):
            return ("word", token)
        raise _InvalidPdf("Invalid PDF value")


def _dictionary_fingerprint(value: tuple[Any, ...]) -> dict[bytes, tuple[Any, ...]]:
    if value[0] != "dict":
        raise _InvalidPdf("Expected a dictionary")
    return dict(value[1])


def _references(value: tuple[Any, ...]):
    if value[0] == "ref":
        yield value[1], value[2]
    elif value[0] == "array":
        for child in value[1]:
            yield from _references(child)
    elif value[0] == "dict":
        for _, child in value[1]:
            yield from _references(child)


def _check_xref_ranges(ranges: list[tuple[int, int]]) -> None:
    end = 0
    for first, count in sorted(ranges):
        if count == 0:
            continue
        if first < end:
            raise _InvalidPdf("Overlapping cross-reference ranges")
        end = first + count


def _stream_xref_ranges(syntax: dict[bytes, tuple[Any, ...]]) -> list[tuple[int, int]]:
    index = syntax.get(b"Index")
    if index is None:
        # The default is the single range from zero to /Size.
        return []
    if index[0] != "array" or len(index[1]) % 2:
        raise _InvalidPdf("Invalid cross-reference stream index")
    values = []
    for item in index[1]:
        if item[0] != "number" or item[1] < 0 or item[1] != item[1].to_integral_value():
            raise _InvalidPdf("Invalid cross-reference stream range")
        values.append(int(item[1]))
    return list(zip(values[::2], values[1::2]))


def _xref_section(
    data: bytes, offset: int
) -> tuple[int, dict[bytes, tuple[Any, ...]], bool]:
    lexer = _Lexer(data, offset)
    _, token = lexer.token()
    is_stream = token != b"xref"
    ranges = []
    if is_stream:
        match = _HEADER.match(data, offset)
        if match is None:
            raise _InvalidPdf("Invalid cross-reference stream header")
        lexer.position = match.end()
    else:
        while True:
            kind, token = lexer.token()
            if token == b"trailer":
                break
            if kind != "number" or not _INTEGER.fullmatch(token):
                raise _InvalidPdf("Invalid cross-reference subsection")
            count_kind, count = lexer.token()
            if count_kind != "number" or not _INTEGER.fullmatch(count):
                raise _InvalidPdf("Invalid cross-reference count")
            ranges.append((int(token), int(count)))
            for _ in range(int(count)):
                first_kind, first = lexer.token()
                second_kind, second = lexer.token()
                status_kind, status = lexer.token()
                if (
                    first_kind != "number"
                    or second_kind != "number"
                    or not _INTEGER.fullmatch(first)
                    or not _INTEGER.fullmatch(second)
                    or status_kind != "word"
                    or status not in (b"n", b"f")
                ):
                    raise _InvalidPdf("Invalid cross-reference entry")
    lexer.skip()
    start = lexer.position
    syntax = _dictionary_fingerprint(lexer.value())
    if is_stream:
        ranges = _stream_xref_ranges(syntax)
    _check_xref_ranges(ranges)
    return start, syntax, is_stream


def _xref_chain(data: bytes) -> list[int]:
    # Both supported pypdf versions use these methods while reading a PDF.
    # Use an independent stream before PdfReader follows any /Prev links.
    probe = object.__new__(PdfReader)
    probe.strict = True
    stream = BytesIO(data)
    stream.seek(0, 2)
    probe._find_eof_marker(stream)
    offset = probe._find_startxref_pos(stream)
    offsets: list[int] = []
    visited: set[int] = set()
    while True:
        if not isinstance(offset, int) or offset < 0 or offset >= len(data):
            raise _InvalidPdf("Invalid cross-reference offset")
        if offset in visited:
            raise _InvalidPdf("Cyclic cross-reference chain")
        visited.add(offset)
        offsets.append(offset)
        _, syntax, _ = _xref_section(data, offset)
        if b"XRefStm" in syntax or b"Encrypt" in syntax:
            raise _InvalidPdf("Unsupported hybrid or encrypted PDF")
        previous = syntax.get(b"Prev")
        if previous is None:
            return offsets
        if previous[0] != "number" or previous[1] != previous[1].to_integral_value():
            raise _InvalidPdf("Invalid previous cross-reference")
        offset = int(previous[1])


class _PdfView:
    def __init__(self, data: bytes):
        self.data = data
        self.xrefs = _xref_chain(data)
        self.reader = PdfReader(BytesIO(data), strict=True)
        if self.reader.is_encrypted:
            raise _InvalidPdf("Encrypted PDF")
        self.fingerprints: dict[tuple[int, int], tuple[Any, ...]] = {}
        self.page_refs: set[tuple[int, int]] = set()
        self.trailer: dict[bytes, Any] = {}
        self.trailer_syntax: dict[bytes, tuple[Any, ...]] = {}
        self._read_trailers()
        self._read_pages()
        self._validate_reachable()

    def _validate_stream(self, stream: StreamObject) -> None:
        if not isinstance(stream._data, bytes) or "/F" in stream:
            raise _InvalidPdf("Unreadable or external stream")
        filters = stream.get("/Filter")
        if isinstance(filters, IndirectObject):
            filters = self.resolve(filters)
        parameters = stream.get("/DecodeParms")
        if isinstance(parameters, IndirectObject):
            parameters = self.resolve(parameters)
        if filters is None:
            if parameters is not None:
                raise _InvalidPdf("Decode parameters without a filter")
            return
        filters = list(filters) if isinstance(filters, ArrayObject) else [filters]
        if not filters:
            raise _InvalidPdf("Empty stream filter list")
        if parameters is None:
            parameters = [None] * len(filters)
        elif isinstance(parameters, ArrayObject):
            parameters = list(parameters)
        else:
            parameters = [parameters]
        if len(parameters) != len(filters):
            raise _InvalidPdf("Ambiguous stream decode parameters")
        for index, (filter_name, parameter) in enumerate(zip(filters, parameters)):
            if isinstance(filter_name, IndirectObject):
                filter_name = self.resolve(filter_name)
            if isinstance(parameter, IndirectObject):
                parameter = self.resolve(parameter)
            if not isinstance(filter_name, NameObject) or not (
                parameter is None
                or isinstance(parameter, (DictionaryObject, NullObject))
            ):
                raise _InvalidPdf("Invalid stream filter")
            if index == 0 and filter_name in ("/FlateDecode", "/Fl"):
                decoder = zlib.decompressobj()
                data = stream._data
                for start in range(0, len(data), _FLATE_CHUNK_SIZE):
                    end = min(start + _FLATE_CHUNK_SIZE, len(data))
                    remaining = data[start:end]
                    while remaining:
                        decoder.decompress(remaining, _FLATE_CHUNK_SIZE)
                        if decoder.eof:
                            # A finished decoder cannot consume trailing bytes.
                            if decoder.unused_data or end != len(data):
                                raise _InvalidPdf("Invalid Flate stream")
                            break
                        remaining = decoder.unconsumed_tail
                if not decoder.eof:
                    raise _InvalidPdf("Invalid Flate stream")

    def _read_trailers(self) -> None:
        for offset in self.xrefs:
            start, syntax, is_stream = _xref_section(self.data, offset)
            stream = BytesIO(self.data)
            stream.seek(start)
            trailer = read_object(stream, self.reader)
            if is_stream:
                if (
                    not isinstance(trailer, StreamObject)
                    or trailer.get("/Type") != "/XRef"
                ):
                    raise _InvalidPdf("Invalid cross-reference stream")
                self._validate_stream(trailer)
            else:
                if not isinstance(trailer, DictionaryObject) or isinstance(
                    trailer, StreamObject
                ):
                    raise _InvalidPdf("Invalid trailer")
            size = trailer.get("/Size")
            if not isinstance(size, NumberObject) or size <= 0:
                raise _InvalidPdf("Invalid trailer size")
            structural = _TRAILER_STRUCTURE | (
                _XREF_STREAM_STRUCTURE if is_stream else set()
            )
            for key, value in syntax.items():
                if key not in structural and key not in self.trailer:
                    pdf_key = NameObject("/" + key.decode("latin-1"))
                    if pdf_key not in trailer:
                        raise _InvalidPdf("Unreadable trailer key")
                    self.trailer[key] = trailer.raw_get(pdf_key)
                    self.trailer_syntax[key] = value
            if "/Prev" in trailer and not isinstance(
                trailer.raw_get("/Prev"), NumberObject
            ):
                raise _InvalidPdf("Invalid previous cross-reference")

    def resolve(self, reference: IndirectObject) -> Any:
        if (
            reference.pdf is not self.reader
            or reference.idnum <= 0
            or reference.generation < 0
        ):
            raise _InvalidPdf("Invalid indirect reference")
        reader = self.reader
        if reference.generation == 0 and reference.idnum in reader.xref_objStm:
            stream_id, index = reader.xref_objStm[reference.idnum]
            if (
                not isinstance(stream_id, int)
                or not isinstance(index, int)
                or index < 0
            ):
                raise _InvalidPdf("Invalid object stream reference")
            self._object_stream_source(reference)
        else:
            offsets = reader.xref.get(reference.generation, {})
            if reference.idnum not in offsets or reader.xref_free_entry.get(
                reference.generation, {}
            ).get(reference.idnum, False):
                raise _InvalidPdf("Missing or free indirect reference")
            offset = offsets[reference.idnum]
            match = _HEADER.match(self.data, offset)
            if match is None or (int(match[1]), int(match[2])) != (
                reference.idnum,
                reference.generation,
            ):
                raise _InvalidPdf("Incorrect indirect object offset")
        result = reference.get_object()
        if result is None or isinstance(result, IndirectObject):
            raise _InvalidPdf("Unreadable indirect object")
        return result

    def _object_stream_source(self, reference: IndirectObject) -> bytes:
        stream_id, index = self.reader.xref_objStm[reference.idnum]
        if stream_id == reference.idnum:
            raise _InvalidPdf("Self-referencing object stream")
        stream = self.resolve(IndirectObject(stream_id, 0, self.reader))
        if not isinstance(stream, StreamObject) or stream.get("/Type") != "/ObjStm":
            raise _InvalidPdf("Invalid object stream")
        self._validate_stream(stream)
        count = stream.get("/N")
        first = stream.get("/First")
        if not isinstance(count, NumberObject) or not isinstance(first, NumberObject):
            raise _InvalidPdf("Invalid object stream index")
        data = stream.get_data()
        if count <= 0 or first < 0 or first > len(data) or index >= count:
            raise _InvalidPdf("Invalid object stream bounds")
        lexer = _Lexer(data[:first])
        entries = []
        for _ in range(int(count)):
            number_kind, number = lexer.token()
            offset_kind, offset = lexer.token()
            if (
                number_kind != "number"
                or offset_kind != "number"
                or not _INTEGER.fullmatch(number)
                or not _INTEGER.fullmatch(offset)
            ):
                raise _InvalidPdf("Invalid object stream entry")
            entries.append((int(number), int(offset)))
        if lexer.token()[0] != "end" or entries[index][0] != reference.idnum:
            raise _InvalidPdf("Incorrect object stream index")
        if len({number for number, _ in entries}) != len(entries):
            raise _InvalidPdf("Duplicate object stream entry")
        offsets = [offset for _, offset in entries]
        if offsets != sorted(set(offsets)) or first + offsets[-1] >= len(data):
            raise _InvalidPdf("Invalid object stream offsets")
        end = first + offsets[index + 1] if index + 1 < len(entries) else len(data)
        return data[first + offsets[index] : end]

    def fingerprint(self, reference: IndirectObject) -> tuple[Any, ...]:
        key = (reference.idnum, reference.generation)
        if key in self.fingerprints:
            return self.fingerprints[key]
        obj = self.resolve(reference)
        if reference.generation == 0 and reference.idnum in self.reader.xref_objStm:
            lexer = _Lexer(self._object_stream_source(reference))
            result = lexer.value()
            if lexer.token()[0] != "end":
                raise _InvalidPdf("Extra object stream data")
        else:
            offset = self.reader.xref[reference.generation][reference.idnum]
            match = _HEADER.match(self.data, offset)
            if match is None:
                raise _InvalidPdf("Invalid object header")
            lexer = _Lexer(self.data, match.end())
            lexer.skip()
            start = lexer.position
            result = lexer.value()
            stream = self.reader.stream
            stream.seek(start)
            parsed = read_object(stream, self.reader)
            if isinstance(obj, StreamObject) != isinstance(parsed, StreamObject):
                raise _InvalidPdf("Inconsistent stream object")
            end_lexer = _Lexer(self.data, stream.tell())
            if end_lexer.token() != ("word", b"endobj"):
                raise _InvalidPdf("Missing object terminator")
        self.fingerprints[key] = result
        return result

    def _read_pages(self) -> None:
        root = self.trailer.get(b"Root")
        if not isinstance(root, IndirectObject):
            raise _InvalidPdf("Missing catalog")
        catalog = self.resolve(root)
        if not isinstance(catalog, DictionaryObject) or isinstance(
            catalog, StreamObject
        ):
            raise _InvalidPdf("Invalid catalog")
        catalog_type = catalog.get("/Type")
        if not isinstance(catalog_type, NameObject) or catalog_type != "/Catalog":
            raise _InvalidPdf("Invalid catalog")
        pages = catalog.get("/Pages")
        if not isinstance(pages, IndirectObject):
            raise _InvalidPdf("Missing page tree")
        visited: set[tuple[int, int]] = set()

        def visit(reference: IndirectObject, parent: IndirectObject | None) -> int:
            key = (reference.idnum, reference.generation)
            if key in visited:
                raise _InvalidPdf("Repeated or cyclic page tree node")
            visited.add(key)
            node = self.resolve(reference)
            if not isinstance(node, DictionaryObject) or isinstance(node, StreamObject):
                raise _InvalidPdf("Invalid page tree node")
            if parent is not None:
                parent_ref = node.get("/Parent")
                if not isinstance(parent_ref, IndirectObject) or (
                    parent_ref.idnum,
                    parent_ref.generation,
                ) != (parent.idnum, parent.generation):
                    raise _InvalidPdf("Invalid page parent")
            node_type = node.get("/Type")
            if not isinstance(node_type, NameObject):
                raise _InvalidPdf("Invalid page tree type")
            if node_type == "/Page":
                if parent is None or "/Kids" in node:
                    raise _InvalidPdf("Invalid page")
                if "/Annots" in node:
                    annotations = node.raw_get("/Annots")
                    if isinstance(annotations, IndirectObject):
                        annotations = self.resolve(annotations)
                    if not isinstance(annotations, ArrayObject):
                        raise _InvalidPdf("Invalid page annotations")
                    for annotation in annotations:
                        if isinstance(annotation, IndirectObject):
                            annotation = self.resolve(annotation)
                        if not isinstance(annotation, DictionaryObject) or isinstance(
                            annotation, StreamObject
                        ):
                            raise _InvalidPdf("Invalid page annotation")
                self.page_refs.add(key)
                return 1
            if node_type != "/Pages":
                raise _InvalidPdf("Invalid page tree type")
            kids = node.get("/Kids")
            if isinstance(kids, IndirectObject):
                kids = self.resolve(kids)
            count = node.get("/Count")
            if not isinstance(kids, ArrayObject) or not isinstance(count, NumberObject):
                raise _InvalidPdf("Invalid page tree children")
            total = 0
            for child in kids:
                if not isinstance(child, IndirectObject):
                    raise _InvalidPdf("Direct page tree child")
                total += visit(child, reference)
            if total != count:
                raise _InvalidPdf("Incorrect page count")
            return total

        if visit(pages, None) == 0:
            raise _InvalidPdf("PDF has no pages")

    def _validate_reachable(self) -> None:
        seen_refs: set[tuple[int, int]] = set()
        seen_containers: set[int] = set()

        def visit(value: Any) -> None:
            if isinstance(value, IndirectObject):
                key = (value.idnum, value.generation)
                if key in seen_refs:
                    return
                seen_refs.add(key)
                obj = self.resolve(value)
                for obj_id, generation in _references(self.fingerprint(value)):
                    visit(IndirectObject(obj_id, generation, self.reader))
                visit(obj)
            elif isinstance(value, DictionaryObject):
                if id(value) in seen_containers:
                    return
                seen_containers.add(id(value))
                if isinstance(value, StreamObject):
                    self._validate_stream(value)
                for key, child in value.items():
                    if not isinstance(key, NameObject):
                        raise _InvalidPdf("Invalid dictionary key")
                    visit(child)
            elif isinstance(value, ArrayObject):
                if id(value) in seen_containers:
                    return
                seen_containers.add(id(value))
                for child in value:
                    visit(child)
            elif isinstance(value, FloatObject):
                if not math.isfinite(value):
                    raise _InvalidPdf("Invalid number")
            elif not isinstance(
                value,
                (
                    NameObject,
                    TextStringObject,
                    ByteStringObject,
                    NumberObject,
                    BooleanObject,
                    NullObject,
                ),
            ):
                raise _InvalidPdf("Unsupported PDF object")

        for value in self.trailer.values():
            visit(value)
        for value in self.trailer_syntax.values():
            for obj_id, generation in _references(value):
                visit(IndirectObject(obj_id, generation, self.reader))


class _Comparator:
    def __init__(self, previous: _PdfView, current: _PdfView):
        self.previous = previous
        self.current = current
        self.seen: set[tuple[int, int, bool]] = set()

    def equal(self, left: Any, right: Any, info: bool = False) -> bool:
        if isinstance(left, IndirectObject) or isinstance(right, IndirectObject):
            if not isinstance(left, IndirectObject) or not isinstance(
                right, IndirectObject
            ):
                return False
            key = (left.idnum, left.generation)
            if key != (right.idnum, right.generation):
                return False
            left_page = key in self.previous.page_refs
            if left_page != (key in self.current.page_refs):
                return False
            marker = (*key, info)
            if marker in self.seen:
                return True
            self.seen.add(marker)
            left_fp = self.previous.fingerprint(left)
            right_fp = self.current.fingerprint(right)
            if left_page or info:
                excluded = ({b"Annots"} if left_page else set()) | (
                    {b"ModDate"} if info else set()
                )
                left_fp = (
                    "dict",
                    tuple(
                        (k, v)
                        for k, v in _dictionary_fingerprint(left_fp).items()
                        if k not in excluded
                    ),
                )
                right_fp = (
                    "dict",
                    tuple(
                        (k, v)
                        for k, v in _dictionary_fingerprint(right_fp).items()
                        if k not in excluded
                    ),
                )
            if left_fp != right_fp:
                return False
            return self.equal(
                self.previous.resolve(left), self.current.resolve(right), info=info
            )
        if isinstance(left, DictionaryObject) or isinstance(right, DictionaryObject):
            if not isinstance(left, DictionaryObject) or not isinstance(
                right, DictionaryObject
            ):
                return False
            if isinstance(left, StreamObject) != isinstance(right, StreamObject):
                return False
            if isinstance(left, StreamObject) and left._data != right._data:
                return False
            left_ref = getattr(left, "indirect_reference", None)
            right_ref = getattr(right, "indirect_reference", None)
            is_page = (
                isinstance(left_ref, IndirectObject)
                and isinstance(right_ref, IndirectObject)
                and (left_ref.idnum, left_ref.generation) in self.previous.page_refs
                and (right_ref.idnum, right_ref.generation) in self.current.page_refs
            )
            excluded = ({"/Annots"} if is_page else set()) | (
                {"/ModDate"} if info else set()
            )
            left_keys = set(left) - excluded
            if left_keys != set(right) - excluded:
                return False
            return all(
                self.equal(left.raw_get(key), right.raw_get(key)) for key in left_keys
            )
        if isinstance(left, ArrayObject) or isinstance(right, ArrayObject):
            return (
                isinstance(left, ArrayObject)
                and isinstance(right, ArrayObject)
                and len(left) == len(right)
                and all(self.equal(a, b) for a, b in zip(left, right))
            )
        if isinstance(left, (NumberObject, FloatObject)) and isinstance(
            right, (NumberObject, FloatObject)
        ):
            return left == right
        if type(left) is not type(right):
            return False
        if isinstance(left, TextStringObject):
            return left.original_bytes == right.original_bytes
        if isinstance(left, BooleanObject):
            return left.value == right.value
        if isinstance(left, NullObject):
            return True
        return left == right


def _file_identifier(view: _PdfView) -> tuple[bytes, bytes] | None:
    if b"ID" not in view.trailer:
        return None
    value = view.trailer[b"ID"]
    if isinstance(value, IndirectObject):
        value = view.resolve(value)
    if not isinstance(value, ArrayObject) or len(value) != 2:
        raise _InvalidPdf("Invalid file identifier")
    identifiers = []
    for item in value:
        if isinstance(item, IndirectObject):
            item = view.resolve(item)
        if isinstance(item, TextStringObject):
            identifiers.append(item.original_bytes)
        elif isinstance(item, ByteStringObject):
            identifiers.append(bytes(item))
        else:
            raise _InvalidPdf("Invalid file identifier string")
    return identifiers[0], identifiers[1]


def annotation_only_revision(
    pdf_bytes: bytes, previous_size: int, previous_sha256: str
) -> bool:
    """Check whether an incremental PDF revision changes only annotations.

    Parameters
    ----------
    pdf_bytes : bytes
        Complete current PDF bytes.
    previous_size : int
        Byte length of the previous PDF, which must be a strict prefix.
    previous_sha256 : str
        SHA-256 hex digest of the previous PDF bytes.

    Returns
    -------
    bool
        True when both PDFs pass the comparison. False also covers errors.

    Notes
    -----
    The caller must maintain identity proof separately. This function does not
    establish or upgrade proof. The comparison can reject harmless rewrites.
    A saved revision can add a file identifier or change its second value.
    The first value must remain unchanged when the previous PDF has one.
    The comparator checks opaque stream bytes without decoding them.
    """
    try:
        if (
            type(pdf_bytes) is not bytes
            or type(previous_size) is not int
            or not isinstance(previous_sha256, str)
        ):
            return False
        if (
            previous_size <= 0
            or previous_size >= len(pdf_bytes)
            or not _HASH.fullmatch(previous_sha256)
        ):
            return False
        prefix = pdf_bytes[:previous_size]
        if not hmac.compare_digest(
            hashlib.sha256(prefix).hexdigest(), previous_sha256.lower()
        ):
            return False
        previous = _PdfView(prefix)
        current = _PdfView(pdf_bytes)
        if (
            current.xrefs[0] < previous_size
            or previous.xrefs[0] not in current.xrefs[1:]
        ):
            return False
        old_id, new_id = _file_identifier(previous), _file_identifier(current)
        if old_id is not None and (new_id is None or old_id[0] != new_id[0]):
            return False
        if set(previous.trailer) - {b"ID"} != set(current.trailer) - {b"ID"}:
            return False
        comparator = _Comparator(previous, current)
        for key in previous.trailer:
            if key == b"ID":
                continue
            if previous.trailer_syntax[key] != current.trailer_syntax[key]:
                return False
            left, right = previous.trailer[key], current.trailer[key]
            if key == b"Info":
                if not isinstance(left, IndirectObject) or not isinstance(
                    right, IndirectObject
                ):
                    return False
                old_info, new_info = previous.resolve(left), current.resolve(right)
                if not isinstance(old_info, DictionaryObject) or isinstance(
                    old_info, StreamObject
                ):
                    return False
                if not isinstance(new_info, DictionaryObject) or isinstance(
                    new_info, StreamObject
                ):
                    return False
            if not comparator.equal(left, right, info=key == b"Info"):
                return False
        return True
    except Exception:  # noqa: BLE001 - All parser failures must fail closed.
        return False
