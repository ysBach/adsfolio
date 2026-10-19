"""Annotation extraction and porting helpers for PDF upgrades."""

import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PORTABLE_TYPE_NAMES = {
    8: "Highlight",
    9: "Underline",
    10: "Squiggly",
    11: "StrikeOut",
}
CONTEXT_SIZES = [80, 60, 40, 25, 15, 8]

__all__ = [
    "AnnotationRecord",
    "MatchedAnnotation",
    "PortingReport",
    "apply_annotations",
    "extract_annotations",
    "match_annotations",
    "normalize_text",
    "port_annotations",
]


@dataclass
class AnnotationRecord:
    """Portable annotation information extracted from the source PDF."""

    annotation_type: str
    text: str
    context_before: str
    context_after: str
    color: Any | None
    content: str
    page_num: int
    opacity: float | None
    type_code: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "annotation_type": self.annotation_type,
            "text": self.text,
            "context_before": self.context_before,
            "context_after": self.context_after,
            "color": self.color,
            "content": self.content,
            "page_num": self.page_num,
            "opacity": self.opacity,
            "type_code": self.type_code,
        }


@dataclass
class MatchedAnnotation:
    """Matched target location for one source annotation."""

    source_index: int
    source: AnnotationRecord
    target_page: int
    target_quads: list[Any]


@dataclass
class PortingReport:
    """Summary of annotation porting results."""

    total_portable: int
    ported: int
    failed: int
    skipped_nonportable: int
    output_path: str
    unported: list[dict[str, Any]] = field(default_factory=list)


def _import_fitz() -> Any:
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Annotation porting requires PyMuPDF. Install with adsfolio[full]."
        ) from exc
    return fitz


def normalize_text(text: str) -> str:
    """Normalize text for robust matching across PDF render variants."""
    if not text:
        return ""
    normalized = text.replace("\u00ad", "")
    normalized = re.sub(r"-\s*\n\s*", "", normalized)
    normalized = normalized.replace("\n", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _iter_annots(page: Any) -> Iterable[Any]:
    annots = page.annots()
    if annots is None:
        return []
    return annots


def _extract_annotation_text(page: Any, annot: Any) -> str:
    text = page.get_text("text", clip=annot.rect) or ""
    return normalize_text(text)


def _extract_context(
    page_text: str, annotation_text: str, context_chars: int
) -> tuple[str, str]:
    if not page_text or not annotation_text:
        return "", ""
    idx = page_text.find(annotation_text)
    if idx < 0:
        return "", ""
    before = page_text[max(0, idx - context_chars) : idx]
    after = page_text[
        idx + len(annotation_text) : idx + len(annotation_text) + context_chars
    ]
    return before.strip(), after.strip()


def extract_annotations(
    source_path: Path, context_chars: int = 150
) -> list[AnnotationRecord]:
    """Extract portable text annotations and contextual windows."""
    fitz = _import_fitz()
    source = fitz.open(str(source_path))
    records: list[AnnotationRecord] = []
    try:
        for page in source:
            page_text = normalize_text(page.get_text("text") or "")
            for annot in _iter_annots(page):
                type_code = int(annot.type[0])
                if type_code not in PORTABLE_TYPE_NAMES:
                    continue

                annot_text = _extract_annotation_text(page, annot)
                if not annot_text:
                    continue
                context_before, context_after = _extract_context(
                    page_text, annot_text, context_chars=context_chars
                )
                colors = getattr(annot, "colors", {}) or {}
                color = colors.get("stroke") or colors.get("fill")
                info = getattr(annot, "info", {}) or {}
                records.append(
                    AnnotationRecord(
                        annotation_type=PORTABLE_TYPE_NAMES[type_code],
                        text=annot_text,
                        context_before=context_before,
                        context_after=context_after,
                        color=color,
                        content=str(info.get("content", "") or ""),
                        page_num=int(page.number),
                        opacity=getattr(annot, "opacity", None),
                        type_code=type_code,
                    )
                )
    finally:
        source.close()
    return records


def _join_parts(*parts: str) -> str:
    return normalize_text(" ".join(part for part in parts if part))


def _find_unique_page(needle: str, page_texts: list[str]) -> int | None:
    if not needle:
        return None
    matches = [idx for idx, text in enumerate(page_texts) if needle in text]
    if len(matches) != 1:
        return None
    return matches[0]


def _candidate_search_texts(text: str) -> list[str]:
    candidates = [text, normalize_text(text)]
    unique: list[str] = []
    for item in candidates:
        normalized = item.strip()
        if not normalized:
            continue
        if normalized in unique:
            continue
        unique.append(normalized)
    return unique


def _search_quads(page: Any, text: str) -> list[Any] | None:
    for candidate in _candidate_search_texts(text):
        quads = list(page.search_for(candidate, quads=True))
        if len(quads) == 1:
            return quads
    return None


def _match_record(
    record: AnnotationRecord, target_doc: Any, page_texts: list[str]
) -> MatchedAnnotation | None:
    text = normalize_text(record.text)
    before = normalize_text(record.context_before)
    after = normalize_text(record.context_after)

    for size in CONTEXT_SIZES:
        before_tail = before[-size:] if before else ""
        after_head = after[:size] if after else ""
        combined = _join_parts(before_tail, text, after_head)
        page_idx = _find_unique_page(combined, page_texts)
        if page_idx is None:
            continue
        quads = _search_quads(target_doc[page_idx], text)
        if quads is not None:
            return MatchedAnnotation(
                source_index=-1,
                source=record,
                target_page=page_idx,
                target_quads=quads,
            )

    for size in CONTEXT_SIZES:
        before_only = _join_parts(before[-size:] if before else "", text)
        if before_only:
            page_idx = _find_unique_page(before_only, page_texts)
            if page_idx is not None:
                quads = _search_quads(target_doc[page_idx], text)
                if quads is not None:
                    return MatchedAnnotation(
                        source_index=-1,
                        source=record,
                        target_page=page_idx,
                        target_quads=quads,
                    )

        after_only = _join_parts(text, after[:size] if after else "")
        if after_only:
            page_idx = _find_unique_page(after_only, page_texts)
            if page_idx is not None:
                quads = _search_quads(target_doc[page_idx], text)
                if quads is not None:
                    return MatchedAnnotation(
                        source_index=-1,
                        source=record,
                        target_page=page_idx,
                        target_quads=quads,
                    )

    if len(text) >= 30:
        page_idx = _find_unique_page(text, page_texts)
        if page_idx is not None:
            quads = _search_quads(target_doc[page_idx], text)
            if quads is not None:
                return MatchedAnnotation(
                    source_index=-1,
                    source=record,
                    target_page=page_idx,
                    target_quads=quads,
                )

    sentences = [normalize_text(s) for s in re.split(r"(?<=[.!?])\s+", text)]
    for sentence in sentences:
        if len(sentence) < 15:
            continue
        page_idx = _find_unique_page(sentence, page_texts)
        if page_idx is None:
            continue
        quads = _search_quads(target_doc[page_idx], sentence)
        if quads is not None:
            return MatchedAnnotation(
                source_index=-1,
                source=record,
                target_page=page_idx,
                target_quads=quads,
            )
    return None


def match_annotations(
    records: list[AnnotationRecord], target_path: Path
) -> list[MatchedAnnotation]:
    """Match source annotations to text locations in target PDF."""
    fitz = _import_fitz()
    target_doc = fitz.open(str(target_path))
    matched: list[MatchedAnnotation] = []
    try:
        page_texts = [
            normalize_text(page.get_text("text") or "") for page in target_doc
        ]
        for idx, record in enumerate(records):
            found = _match_record(record, target_doc, page_texts)
            if found is None:
                continue
            found.source_index = idx
            matched.append(found)
    finally:
        target_doc.close()
    return matched


def _create_annotation(page: Any, matched: MatchedAnnotation) -> Any | None:
    annotation_type = matched.source.annotation_type
    quads = matched.target_quads
    if annotation_type == "Highlight":
        return page.add_highlight_annot(quads)
    if annotation_type == "Underline":
        return page.add_underline_annot(quads)
    if annotation_type == "StrikeOut":
        return page.add_strikeout_annot(quads)
    if annotation_type == "Squiggly":
        return page.add_squiggly_annot(quads)
    return None


def apply_annotations(
    matched: list[MatchedAnnotation], target_path: Path, output_path: Path
) -> int:
    """Apply matched annotations to target PDF and save output."""
    fitz = _import_fitz()
    target_doc = fitz.open(str(target_path))
    try:
        count = 0
        for item in matched:
            page = target_doc[item.target_page]
            annot = _create_annotation(page, item)
            if annot is None:
                continue
            if item.source.color:
                annot.set_colors(stroke=item.source.color)
            if item.source.opacity is not None:
                annot.set_opacity(item.source.opacity)
            if item.source.content:
                annot.set_info(content=item.source.content)
            annot.update()
            count += 1

        output_path.parent.mkdir(parents=True, exist_ok=True)
        target_doc.save(str(output_path))
        return count
    finally:
        target_doc.close()


def _count_nonportable_annotations(source_path: Path) -> int:
    fitz = _import_fitz()
    source = fitz.open(str(source_path))
    count = 0
    try:
        for page in source:
            for annot in _iter_annots(page):
                type_code = int(annot.type[0])
                if type_code not in PORTABLE_TYPE_NAMES:
                    count += 1
    finally:
        source.close()
    return count


def port_annotations(
    source_path: Path, target_path: Path, output_path: Path
) -> PortingReport:
    """Extract, match, and apply annotations to produce upgraded PDF."""
    records = extract_annotations(source_path)
    matched = match_annotations(records, target_path)
    if matched:
        ported = apply_annotations(
            matched, target_path=target_path, output_path=output_path
        )
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target_path, output_path)
        ported = 0

    matched_indices = {item.source_index for item in matched}
    unported_records = [
        records[idx].to_dict()
        for idx in range(len(records))
        if idx not in matched_indices
    ]
    skipped_nonportable = _count_nonportable_annotations(source_path)
    return PortingReport(
        total_portable=len(records),
        ported=ported,
        failed=max(0, len(records) - len(matched_indices)),
        skipped_nonportable=skipped_nonportable,
        output_path=str(output_path),
        unported=unported_records,
    )
