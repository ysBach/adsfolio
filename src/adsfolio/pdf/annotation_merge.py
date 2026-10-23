"""Move annotations between PDF editions of the same paper into a new copy.

The target PDF is never modified. A user places each source annotation on the
target, or waives it explicitly, and the result is written to a new file after
it has been re-read and checked. The companion `annotation_matching` module
finds candidate text locations. Every placement still needs explicit approval.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROVENANCE_KEY = "ADSfolioMergeSource"
MARKUP_SUBTYPES = ("Highlight", "Underline", "StrikeOut", "Squiggly")
GEOMETRY_SUBTYPES = ("Square", "Circle", "Line", "Ink")
NOTE_SUBTYPES = ("Text",)
SUPPORTED_SUBTYPES = MARKUP_SUBTYPES + NOTE_SUBTYPES + GEOMETRY_SUBTYPES
COMPANION_SUBTYPES = ("Popup",)
# MuPDF stores coordinates, colours and opacities as 32-bit floats and writes
# them as decimal text. A value is therefore reproduced to within a few units
# in the last place of a float32 (2**-23 relative); four units cover the
# decimal round trip and one addition when a shape is translated.
_FLOAT32_EPS = 2.0**-23
_ULPS = 4

Point = tuple[float, float]
RectTuple = tuple[float, float, float, float]

__all__ = [
    "AnnotationItem",
    "AnnotationMergeError",
    "MergeInput",
    "MergeReport",
    "MissingDependencyError",
    "Placement",
    "WordIndex",
    "build_merged_copy",
    "enumerate_annotations",
    "import_fitz",
    "line_rects",
    "placement_error",
    "suggest_exact_placement",
    "words_inside",
]


class MissingDependencyError(RuntimeError):
    """Raised when PyMuPDF is not installed."""


class AnnotationMergeError(RuntimeError):
    """Raised when a merge cannot be performed or fails verification."""


def import_fitz() -> Any:
    """Import PyMuPDF, or raise `MissingDependencyError` with install advice."""
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MissingDependencyError(
            "Merging annotations needs PyMuPDF. Install it with "
            "'pip install \"adsfolio[full]\"' (the desktop app already includes it)."
        ) from exc
    return fitz


@dataclass(frozen=True)
class AnnotationItem:
    """One annotation found in the source PDF, with the data needed to copy it.

    Coordinates are PDF points with the origin at the top-left of the page, as
    reported by PyMuPDF. `supported` is false for subtypes that cannot be
    copied without approximation; `companion` marks popup windows, which
    belong to a parent annotation and carry no content of their own.
    """

    item_id: str
    provenance: str
    source_sha256: str
    page: int
    xref: int
    subtype: str
    supported: bool
    companion: bool
    reason: str
    rect: RectTuple
    content: str = ""
    title: str = ""
    subject: str = ""
    icon_name: str = ""
    stroke: tuple[float, ...] = ()
    fill: tuple[float, ...] = ()
    opacity: float = -1.0
    flags: int = 0
    border_width: float | None = None
    dashes: tuple[float, ...] = ()
    quads: tuple[tuple[Point, ...], ...] = ()
    vertices: tuple[Point, ...] = ()
    ink: tuple[tuple[Point, ...], ...] = ()
    line_ends: tuple[int, int] | None = None
    text: str = ""
    page_rotation: int = 0

    @property
    def kind(self) -> str:
        """Return `"markup"`, `"point"` or `"none"` for the placement mode."""
        if not self.supported:
            return "none"
        return "markup" if self.subtype in MARKUP_SUBTYPES else "point"

    @property
    def needs_waiver(self) -> bool:
        """Return whether this item must be explicitly waived to be left out."""
        return not self.supported and not self.companion


@dataclass(frozen=True)
class Placement:
    """Where one source annotation goes in the target.

    Markup annotations use `rects` (word-line boxes on `page`). Other
    supported annotations use `point`, the target position of the source
    annotation's top-left corner; the shape is translated, never scaled.
    """

    item_id: str
    page: int
    rects: tuple[RectTuple, ...] = ()
    point: Point | None = None


@dataclass(frozen=True)
class MergeInput:
    """An original PDF path and the exact content used for a saved merge."""

    path: str
    sha256: str


@dataclass
class MergeReport:
    """Outcome of one merge. `complete` is true only if nothing is left over."""

    output_path: str
    transferred: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)
    waived: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    companions: list[str] = field(default_factory=list)
    verified: bool = False
    input_files: tuple[MergeInput, ...] = ()
    output_sha256: str = ""

    @property
    def complete(self) -> bool:
        """Return whether every source annotation was transferred or waived."""
        return self.verified and not self.unresolved

    def summary(self) -> str:
        """Return a plain-text summary that never calls a partial merge complete."""
        lines = [
            f"Saved: {self.output_path}",
            f"Transferred and verified: {len(self.transferred)}",
            f"Already present from an earlier merge: {len(self.already_present)}",
            f"Explicitly waived: {len(self.waived)}",
            f"Popup windows not copied (parent content is copied): {len(self.companions)}",
        ]
        if self.unresolved:
            lines.append(
                f"UNRESOLVED: {len(self.unresolved)} annotation(s) were neither "
                "placed nor waived. This is a partial merge, not a full "
                "consolidation."
            )
        else:
            lines.append("Every source annotation was transferred or waived.")
        return "\n".join(lines)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open(fitz: Any, path: Path) -> Any:
    doc = fitz.open(str(path))
    if doc.needs_pass:
        doc.close()
        raise AnnotationMergeError(f"{path.name} is encrypted; decrypt it first.")
    return doc


def _points(values: Any) -> tuple[Point, ...]:
    return tuple((float(p[0]), float(p[1])) for p in values)


def _subtype(doc: Any, xref: int) -> str:
    return doc.xref_get_key(xref, "Subtype")[1].lstrip("/") or "Unknown"


def _merged_keys(doc: Any, page: Any) -> list[tuple[int, str]]:
    """Return (xref, provenance) for annotations on `page` that were merged in."""
    found = []
    for xref, _code, _name in page.annot_xrefs():
        kind, value = doc.xref_get_key(xref, PROVENANCE_KEY)
        if kind == "string":
            found.append((xref, value))
    return found


def line_rects(words: list[tuple[Any, ...]]) -> tuple[RectTuple, ...]:
    """Merge PyMuPDF word tuples into one bounding box per text line."""
    boxes: dict[tuple[int, int], list[float]] = {}
    for word in words:
        key = (int(word[5]), int(word[6]))
        box = boxes.get(key)
        if box is None:
            boxes[key] = [word[0], word[1], word[2], word[3]]
        else:
            box[0], box[1] = min(box[0], word[0]), min(box[1], word[1])
            box[2], box[3] = max(box[2], word[2]), max(box[3], word[3])
    return tuple((b[0], b[1], b[2], b[3]) for b in boxes.values())


def _words_in(words: list[tuple[Any, ...]], rect: RectTuple) -> list[tuple[Any, ...]]:
    x0, y0, x1, y1 = rect
    return [
        w
        for w in words
        if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1
    ]


def words_inside(page: Any, rect: RectTuple) -> list[tuple[Any, ...]]:
    """Return the words of `page` whose centres lie inside `rect`."""
    return _words_in(page.get_text("words"), rect)


def _markup_text(
    words: list[tuple[Any, ...]], quads: tuple[tuple[Point, ...], ...]
) -> str:
    found: list[str] = []
    for quad in quads:
        xs, ys = [p[0] for p in quad], [p[1] for p in quad]
        box = (min(xs), min(ys), max(xs), max(ys))
        found.extend(w[4] for w in _words_in(words, box))
    return " ".join(found)


def enumerate_annotations(path: Path) -> list[AnnotationItem]:
    """List every annotation object in `path`, supported or not.

    Links, form widgets, popups and all other objects in a page's annotation
    array are included, so nothing is skipped silently. Annotations that an
    earlier merge added to this file are listed like any other.
    """
    fitz = import_fitz()
    sha = _sha256(path)
    doc = _open(fitz, path)
    items: list[AnnotationItem] = []
    try:
        for page in doc:
            page_words = None
            for xref, _code, _name in page.annot_xrefs():
                subtype = _subtype(doc, xref)
                base = {
                    "item_id": f"{sha[:16]}:{page.number}:{xref}",
                    "provenance": hashlib.sha256(
                        f"{sha}|{page.number}|{xref}|{subtype}".encode()
                    ).hexdigest(),
                    "source_sha256": sha,
                    "page": page.number,
                    "xref": xref,
                    "subtype": subtype,
                    "page_rotation": int(page.rotation),
                }
                if subtype not in SUPPORTED_SUBTYPES:
                    companion = subtype in COMPANION_SUBTYPES
                    reason = (
                        "Popup window; its parent annotation carries the content"
                        if companion
                        else f"{subtype} cannot be copied without approximation"
                    )
                    raw = doc.xref_get_key(xref, "Rect")[1]
                    nums = [float(v) for v in raw.strip("[]").split()] if raw else []
                    items.append(
                        AnnotationItem(
                            **base,
                            supported=False,
                            companion=companion,
                            reason=reason,
                            rect=tuple(nums) if len(nums) == 4 else (0, 0, 0, 0),
                        )
                    )
                    continue
                annot = page.load_annot(xref)
                if subtype in MARKUP_SUBTYPES and page_words is None:
                    page_words = page.get_text("words")
                info = annot.info or {}
                colors = annot.colors or {}
                border = annot.border or {}
                raw_vertices = annot.vertices or ()
                vertices = _points(raw_vertices) if subtype != "Ink" else ()
                quads = (
                    tuple(
                        tuple(vertices[i : i + 4]) for i in range(0, len(vertices), 4)
                    )
                    if subtype in MARKUP_SUBTYPES
                    else ()
                )
                ink = (
                    tuple(_points(stroke) for stroke in raw_vertices)
                    if subtype == "Ink"
                    else ()
                )
                supported, reason = True, ""
                if subtype in MARKUP_SUBTYPES and (
                    not quads or any(len(q) != 4 for q in quads)
                ):
                    supported, reason = False, "Markup has no usable QuadPoints"
                elif subtype == "Line" and len(vertices) != 2:
                    supported, reason = False, "Line has no two end points"
                elif subtype == "Ink" and not ink:
                    supported, reason = False, "Ink has no strokes"
                items.append(
                    AnnotationItem(
                        **base,
                        supported=supported,
                        companion=False,
                        reason=reason,
                        rect=tuple(float(v) for v in annot.rect),
                        content=str(info.get("content") or ""),
                        title=str(info.get("title") or ""),
                        subject=str(info.get("subject") or ""),
                        icon_name=str(info.get("name") or ""),
                        stroke=tuple(colors.get("stroke") or ()),
                        fill=tuple(colors.get("fill") or ()),
                        opacity=float(annot.opacity),
                        flags=int(annot.flags),
                        border_width=border.get("width"),
                        dashes=tuple(border.get("dashes") or ()),
                        quads=quads,
                        vertices=vertices if subtype == "Line" else (),
                        ink=ink,
                        line_ends=(
                            tuple(annot.line_ends) if subtype == "Line" else None
                        ),
                        text=_markup_text(page_words, quads) if quads else "",
                    )
                )
            items.extend(_unreadable_entries(doc, page, sha, items))
    finally:
        doc.close()
    return items


def _unreadable_entries(
    doc: Any, page: Any, sha: str, items: list[AnnotationItem]
) -> list[AnnotationItem]:
    """Report entries of the page's annotation array that PyMuPDF did not list."""
    kind, value = doc.xref_get_key(page.xref, "Annots")
    if kind == "xref":
        value = doc.xref_object(int(value.split()[0]), compressed=False)
    elif kind != "array":
        return []
    listed = {x for x, _c, _n in page.annot_xrefs()}
    missing = [int(n) for n in re.findall(r"(\d+) 0 R", value) if int(n) not in listed]
    return [
        AnnotationItem(
            item_id=f"{sha[:16]}:{page.number}:{xref}",
            provenance=hashlib.sha256(
                f"{sha}|{page.number}|{xref}|Unreadable".encode()
            ).hexdigest(),
            source_sha256=sha,
            page=page.number,
            xref=xref,
            subtype="Unreadable",
            supported=False,
            companion=False,
            reason="Listed in the page's annotations but not readable as an annotation",
            rect=(0, 0, 0, 0),
        )
        for xref in missing
    ]


class WordIndex:
    """Words of every page of one PDF, read once for repeated exact lookups."""

    def __init__(self, path: Path) -> None:
        fitz = import_fitz()
        doc = _open(fitz, path)
        try:
            self.pages: list[list[tuple[Any, ...]]] = [
                page.get_text("words") for page in doc
            ]
        finally:
            doc.close()
        self.tokens = [[w[4] for w in words] for words in self.pages]


def suggest_exact_placement(item: AnnotationItem, index: WordIndex) -> Placement | None:
    """Suggest a markup placement when the source words occur exactly once.

    The source words must appear as the identical run of words exactly once in
    the whole target. Case, punctuation and hyphenation must agree; anything
    else returns `None`. The caller must still ask the user to confirm.
    """
    needle = item.text.split()
    if item.kind != "markup" or not needle:
        return None
    hit: tuple[int, int] | None = None
    for number, tokens in enumerate(index.tokens):
        for i in range(len(tokens) - len(needle) + 1):
            if tokens[i : i + len(needle)] == needle:
                if hit is not None:
                    return None
                hit = (number, i)
    if hit is None:
        return None
    number, i = hit
    return Placement(
        item.item_id, number, rects=line_rects(index.pages[number][i : i + len(needle)])
    )


def _translated_rect(item: AnnotationItem, point: Point) -> RectTuple:
    dx, dy = point[0] - item.rect[0], point[1] - item.rect[1]
    r = item.rect
    return (r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy)


def placement_error(
    item: AnnotationItem,
    placement: Placement,
    page_rect: RectTuple,
    page_rotation: int = 0,
) -> str | None:
    """Return why `placement` is invalid for `item`, or `None` if it is valid."""
    if not item.supported:
        return "This annotation type cannot be placed."
    if item.kind == "markup":
        if not placement.rects:
            return "Select at least one whole word."
        for x0, y0, x1, y1 in placement.rects:
            if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
                return "A selected box has a non-finite coordinate."
            if not (x1 > x0 and y1 > y0):
                return "A selected box is empty."
            if (
                x0 < page_rect[0]
                or y0 < page_rect[1]
                or x1 > page_rect[2]
                or y1 > page_rect[3]
            ):
                return "A selected box lies outside the page."
        return None
    if placement.point is None:
        return "Click a target position."
    if item.page_rotation != page_rotation:
        return (
            "Notes and shapes can only be copied between pages with the same "
            "rotation (not approximated)."
        )
    if not all(math.isfinite(v) for v in placement.point):
        return "The target position is not finite."
    x0, y0, x1, y1 = _translated_rect(item, placement.point)
    if x0 < page_rect[0] or y0 < page_rect[1] or x1 > page_rect[2] or y1 > page_rect[3]:
        return "The annotation would extend beyond the page edge."
    return None


def _create(fitz: Any, page: Any, item: AnnotationItem, placement: Placement) -> None:
    if item.kind == "markup":
        make = {
            "Highlight": page.add_highlight_annot,
            "Underline": page.add_underline_annot,
            "StrikeOut": page.add_strikeout_annot,
            "Squiggly": page.add_squiggly_annot,
        }[item.subtype]
        annot = make(quads=[fitz.Rect(*r).quad for r in placement.rects])
    else:
        point = placement.point
        assert point is not None
        dx, dy = point[0] - item.rect[0], point[1] - item.rect[1]
        rect = fitz.Rect(*_translated_rect(item, point))
        if item.subtype == "Text":
            annot = page.add_text_annot(
                fitz.Point(rect.x0, rect.y0),
                item.content,
                icon=item.icon_name or "Note",
            )
        elif item.subtype in ("Square", "Circle"):
            add = (
                page.add_rect_annot
                if item.subtype == "Square"
                else page.add_circle_annot
            )
            annot = add(rect)
        elif item.subtype == "Line":
            (sx, sy), (ex, ey) = item.vertices
            annot = page.add_line_annot(
                fitz.Point(sx + dx, sy + dy), fitz.Point(ex + dx, ey + dy)
            )
            if item.line_ends is not None:
                annot.set_line_ends(*item.line_ends)
        else:
            annot = page.add_ink_annot(
                [[(x + dx, y + dy) for x, y in stroke] for stroke in item.ink]
            )
    if item.stroke or item.fill:
        annot.set_colors(stroke=list(item.stroke) or None, fill=list(item.fill) or None)
    if item.opacity >= 0:
        annot.set_opacity(item.opacity)
    if item.border_width is not None and item.subtype in GEOMETRY_SUBTYPES:
        annot.set_border(width=item.border_width, dashes=list(item.dashes))
    annot.set_flags(item.flags)
    annot.set_info(content=item.content, title=item.title, subject=item.subject)
    annot.update()
    if item.subtype in ("Square", "Circle"):
        # MuPDF pads these by half the border width; reposition so the final
        # annotation rectangle equals the translated source rectangle.
        pad = float(
            page.parent.xref_get_key(annot.xref, "RD")[1].strip("[]").split()[0]
        )
        wanted = fitz.Rect(*_translated_rect(item, placement.point))
        if tuple(annot.rect) != tuple(wanted):
            annot.set_rect(wanted + (pad, pad, -pad, -pad))
            annot.update()
    page.parent.xref_set_key(annot.xref, PROVENANCE_KEY, f"({item.provenance})")


def _close(a: float, b: float) -> bool:
    scale = max(1.0, abs(a), abs(b))
    return abs(a - b) <= _ULPS * _FLOAT32_EPS * scale


def _all_close(got: Any, want: Any) -> bool:
    return len(got) == len(want) and all(_close(a, b) for a, b in zip(got, want))


def _verify_item(doc: Any, item: AnnotationItem, placement: Placement) -> list[str]:
    """Re-read the saved copy and compare the new annotation with the source."""
    label = f"p{item.page + 1} {item.subtype}"
    page = doc[placement.page]
    matches = [x for x, key in _merged_keys(doc, page) if key == item.provenance]
    if len(matches) != 1:
        return [f"{label}: expected one copy in the saved file, found {len(matches)}"]
    found = page.load_annot(matches[0])
    errors = []
    info = found.info or {}
    if found.type[1] != item.subtype:
        errors.append(f"{label}: subtype is {found.type[1]}")
    for key, want in (
        ("content", item.content),
        ("title", item.title),
        ("subject", item.subject),
    ):
        if (info.get(key) or "") != want:
            errors.append(f"{label}: {key} differs")
    if item.subtype == "Text" and (info.get("name") or "") != (
        item.icon_name or "Note"
    ):
        errors.append(f"{label}: icon differs")
    colors = found.colors or {}
    for key, want in (("stroke", item.stroke), ("fill", item.fill)):
        if want and not _all_close(tuple(colors.get(key) or ()), want):
            errors.append(f"{label}: {key} colour differs")
    if item.opacity >= 0 and not _close(found.opacity, item.opacity):
        errors.append(f"{label}: opacity differs")
    if int(found.flags) != item.flags:
        errors.append(f"{label}: flags differ")
    if item.border_width is not None and item.subtype in GEOMETRY_SUBTYPES:
        border = found.border or {}
        if not _close(border.get("width") or 0, item.border_width) or tuple(
            border.get("dashes") or ()
        ) != tuple(item.dashes):
            errors.append(f"{label}: border differs")
    if item.kind == "markup":
        verts = _points(found.vertices or ())
        got = []
        for i in range(0, len(verts), 4):
            xs = [p[0] for p in verts[i : i + 4]]
            ys = [p[1] for p in verts[i : i + 4]]
            got.append((min(xs), min(ys), max(xs), max(ys)))
        if len(got) != len(placement.rects) or not all(
            _all_close(g, w) for g, w in zip(got, placement.rects)
        ):
            errors.append(f"{label}: placed quads differ")
        return errors
    point = placement.point
    assert point is not None
    dx, dy = point[0] - item.rect[0], point[1] - item.rect[1]
    want = _translated_rect(item, point)
    rect = tuple(found.rect)
    if item.subtype == "Text":
        position_ok = _all_close(rect[:2], want[:2])
    else:
        position_ok = _all_close(rect, want)
    if not position_ok:
        errors.append(f"{label}: position differs")
    if item.subtype == "Line":
        verts = _points(found.vertices or ())
        want_v = [(x + dx, y + dy) for x, y in item.vertices]
        if len(verts) != 2 or not all(_all_close(v, w) for v, w in zip(verts, want_v)):
            errors.append(f"{label}: end points differ")
        if tuple(found.line_ends) != item.line_ends:
            errors.append(f"{label}: line ends differ")
    if item.subtype == "Ink":
        strokes = [_points(s) for s in found.vertices or ()]
        want_s = [[(x + dx, y + dy) for x, y in s] for s in item.ink]
        if len(strokes) != len(want_s) or not all(
            len(s) == len(w) and all(_all_close(p, q) for p, q in zip(s, w))
            for s, w in zip(strokes, want_s)
        ):
            errors.append(f"{label}: ink strokes differ")
    return errors


def _snapshot(doc: Any) -> dict[int, tuple[str, bytes]]:
    """Capture every existing annotation object and its appearance stream.

    The raw object text covers all keys (contents, colours, geometry, flags,
    popups, provenance); the appearance stream bytes cover what is drawn.
    """
    snap: dict[int, tuple[str, bytes]] = {}
    for page in doc:
        for xref, _code, _name in page.annot_xrefs():
            kind, value = doc.xref_get_key(xref, "AP/N")
            stream = b""
            if kind == "xref":
                stream = doc.xref_stream_raw(int(value.split()[0])) or b""
            snap[xref] = (doc.xref_object(xref, compressed=False), stream)
    return snap


def _page_annot_order(doc: Any) -> list[list[int]]:
    return [[x for x, _c, _n in page.annot_xrefs()] for page in doc]


def build_merged_copy(
    target_path: Path,
    source_path: Path,
    items: list[AnnotationItem],
    placements: dict[str, Placement],
    waived: set[str],
    output_path: Path,
) -> MergeReport:
    """Write a verified copy of the target with the placed source annotations.

    Parameters
    ----------
    target_path, source_path : `Path`
        The canonical PDF and the PDF whose annotations are copied. Both are
        only read.
    items : list of `AnnotationItem`
        Result of `enumerate_annotations` for `source_path`.
    placements : dict
        User placements keyed by `item_id`.
    waived : set of `str`
        `item_id` values the user chose not to copy. Only unsupported,
        non-popup items may be waived.
    output_path : `Path`
        New file. An existing path is never overwritten, and its parent
        directory must already exist.

    Returns
    -------
    MergeReport
        Items neither placed nor waived are listed as unresolved. Nothing is
        written unless every transferred annotation re-reads correctly;
        otherwise `AnnotationMergeError` is raised.

    Notes
    -----
    Each copied annotation carries a private provenance key (hash of the
    source file, page and object number). A later merge of the same source
    into the merged copy reports those items as already present instead of
    adding duplicates. Provenance is lost if another tool rewrites the
    annotation, and it does not link different files of one paper.
    """
    fitz = import_fitz()
    if target_path.resolve() == source_path.resolve():
        raise AnnotationMergeError("Target and source must be different files.")
    if output_path.exists() or output_path.is_symlink():
        raise AnnotationMergeError(f"{output_path} already exists; choose a new name.")
    if not output_path.parent.is_dir():
        raise AnnotationMergeError(f"{output_path.parent} does not exist.")
    by_id = {item.item_id: item for item in items}
    sha = _sha256(source_path)
    target_sha = _sha256(target_path)
    if any(item.source_sha256 != sha for item in items):
        raise AnnotationMergeError("The source PDF changed since it was reviewed.")
    current = {i.item_id for i in enumerate_annotations(source_path)}
    if current != set(by_id) or len(by_id) != len(items):
        raise AnnotationMergeError(
            "The annotation list does not match the source PDF; reload it."
        )
    for item_id in waived:
        item = by_id.get(item_id)
        if item is None or item.companion:
            raise AnnotationMergeError("A waiver must name a source annotation.")
    for item_id in placements:
        if item_id not in by_id or not by_id[item_id].supported:
            raise AnnotationMergeError(
                "A placement names an unknown or unsupported item."
            )

    report = MergeReport(output_path=str(output_path))
    doc = _open(fitz, target_path)
    try:
        done_before = {key for page in doc for _x, key in _merged_keys(doc, page)}
        original = _snapshot(doc)
        original_order = _page_annot_order(doc)
        created: list[tuple[AnnotationItem, Placement]] = []
        for item in items:
            if item.companion:
                report.companions.append(item.item_id)
            elif item.item_id in waived:
                report.waived.append(item.item_id)
            elif not item.supported:
                report.unresolved.append(item.item_id)
            elif item.provenance in done_before:
                report.already_present.append(item.item_id)
            elif item.item_id in placements:
                placement = placements[item.item_id]
                if not 0 <= placement.page < doc.page_count:
                    raise AnnotationMergeError("A placement is outside the target.")
                target_page = doc[placement.page]
                problem = placement_error(
                    item,
                    placement,
                    tuple(target_page.rect * target_page.derotation_matrix),
                    int(target_page.rotation),
                )
                if problem:
                    raise AnnotationMergeError(
                        f"p{item.page + 1} {item.subtype}: {problem}"
                    )
                _create(fitz, doc[placement.page], item, placement)
                created.append((item, placement))
            else:
                report.unresolved.append(item.item_id)
        data = doc.tobytes()
    finally:
        doc.close()

    check = fitz.open(stream=data, filetype="pdf")
    try:
        errors: list[str] = []
        for item, placement in created:
            errors.extend(_verify_item(check, item, placement))
        after = _snapshot(check)
        for xref, before in original.items():
            if after.get(xref) != before:
                errors.append(f"Existing target annotation (object {xref}) changed")
        for number, order in enumerate(original_order):
            kept = [x for x in _page_annot_order(check)[number] if x in set(order)]
            if kept != order:
                errors.append(f"Existing annotations on page {number + 1} changed")
        if check.page_count != len(original_order):
            errors.append("Page count changed")
    finally:
        check.close()
    if errors:
        raise AnnotationMergeError("Verification failed:\n" + "\n".join(errors))
    if _sha256(target_path) != target_sha or _sha256(source_path) != sha:
        raise AnnotationMergeError(
            "An original PDF changed while the copy was built. Reload the originals and review again."
        )
    try:
        with output_path.open("xb") as handle:
            handle.write(data)
    except FileExistsError as exc:
        raise AnnotationMergeError(f"{output_path} already exists.") from exc
    report.output_sha256 = hashlib.sha256(data).hexdigest()
    if _sha256(output_path) != report.output_sha256:
        raise AnnotationMergeError(
            "The saved PDF differs from the verified copy. Keep the originals and choose a new output path."
        )
    report.input_files = (
        MergeInput(str(target_path.absolute()), target_sha),
        MergeInput(str(source_path.absolute()), sha),
    )
    report.transferred = [item.item_id for item, _p in created]
    report.verified = True
    return report
