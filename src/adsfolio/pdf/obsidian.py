"""Obsidian-friendly Markdown note generation for tracked PDFs."""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..client import ADSClient

__all__ = ["NotePlan", "ObsidianNoteWriter"]


@dataclass
class NotePlan:
    """One planned Obsidian note for a tracked PDF."""

    original_filename: str
    note_filename: str
    note_path: Path
    pdf_path: Path
    action: str


class ObsidianNoteWriter:
    """Generate Obsidian-friendly Markdown notes from sidecar-tracked PDF metadata."""

    # ADS search returns at most 2000 records per request.
    MAX_BATCH_TERMS = 2000

    def __init__(self, client: "ADSClient | None" = None):
        self.client = client
        self.logger = logging.getLogger(__name__)

    def _first(self, value: Any) -> str:
        if isinstance(value, list):
            return str(value[0]) if value else ""
        if value is None:
            return ""
        return str(value)

    def _chunked(self, items: list[str], chunk_size: int) -> list[list[str]]:
        return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]

    def fetch_missing_abstracts(self, bibcodes: list[str]) -> dict[str, str]:
        """Batch-fetch abstracts for `bibcodes` from ADS. Returns bibcode -> abstract."""
        if self.client is None:
            raise ValueError("Fetching abstracts requires an ADSClient instance.")

        normalized: list[str] = []
        seen: set[str] = set()
        for raw in bibcodes:
            bibcode = str(raw).strip()
            if not bibcode:
                continue
            key = bibcode.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(bibcode)

        abstracts: dict[str, str] = {}
        for chunk in self._chunked(normalized, self.MAX_BATCH_TERMS):
            result = self.client.search.bigquery(
                chunk, fl=["bibcode", "abstract"], rows=len(chunk)
            )
            for doc in result.docs:
                bibcode = self._first(doc.get("bibcode"))
                abstract = str(doc.get("abstract") or "").strip()
                if bibcode and abstract:
                    abstracts[bibcode] = abstract
        return abstracts

    def render_note(self, record: Mapping[str, Any], pdf_path: Path) -> str:
        """Render one Obsidian Markdown note (YAML frontmatter + body) for `record`."""
        bibcode = self._first(record.get("bibcode"))
        title = self._first(record.get("title"))
        year = self._first(record.get("year"))
        journal = self._first(record.get("bibstem")) or self._first(record.get("pub"))
        volume = self._first(record.get("volume"))
        page = self._first(record.get("page"))
        doi = self._first(record.get("doi"))
        abstract = str(record.get("abstract") or "").strip()

        authors_raw = record.get("author")
        if isinstance(authors_raw, list):
            authors = [str(item) for item in authors_raw if item]
        elif authors_raw:
            authors = [str(authors_raw)]
        else:
            authors = []

        frontmatter: dict[str, Any] = {"title": title}
        if authors:
            frontmatter["authors"] = authors
        if year:
            frontmatter["year"] = year
        if journal:
            frontmatter["journal"] = journal
        if volume:
            frontmatter["volume"] = volume
        if page:
            frontmatter["page"] = page
        if bibcode:
            frontmatter["bibcode"] = bibcode
        if doi:
            frontmatter["doi"] = doi
        frontmatter["tags"] = ["paper"]

        import yaml

        yaml_text = yaml.safe_dump(
            frontmatter, sort_keys=False, allow_unicode=True
        ).strip()

        lines = ["---", yaml_text, "---", "", f"# {title}", ""]
        if authors:
            lines.append(f"**Authors:** {', '.join(authors)}")
        if journal and year:
            lines.append(f"**Journal:** {journal} ({year})")
        elif journal:
            lines.append(f"**Journal:** {journal}")
        elif year:
            lines.append(f"**Year:** {year}")
        if bibcode:
            ads_url = f"https://ui.adsabs.harvard.edu/abs/{bibcode}/abstract"
            lines.append(f"**Bibcode:** [{bibcode}]({ads_url})")
        lines.append(f"**PDF:** [Open PDF]({pdf_path.resolve().as_uri()})")
        lines.append("")
        lines.append("## Abstract")
        lines.append("")
        lines.append(abstract if abstract else "_No abstract cached yet._")
        lines.append("")
        return "\n".join(lines)

    _ANNOTATIONS_START = "<!-- adsfolio:pdf-annotations:start -->"
    _ANNOTATIONS_END = "<!-- adsfolio:pdf-annotations:end -->"

    def _annotation_section(self, pdf_path: Path) -> str:
        """Extract PDF markup and text notes into a managed Markdown section."""
        try:
            import fitz
        except ImportError as exc:
            raise ValueError(
                "Exporting PDF annotations requires PyMuPDF. "
                "Install adsfolio with the full extra."
            ) from exc

        lines = [self._ANNOTATIONS_START, "## PDF annotations", ""]
        count = 0
        with fitz.open(pdf_path) as document:
            for page_number, page in enumerate(document, start=1):
                words = None
                for annot in page.annots() or ():
                    kind = annot.type[1]
                    content = (annot.info.get("content") or "").strip()
                    text = ""
                    if kind in {"Highlight", "Underline", "StrikeOut", "Squiggly"}:
                        vertices = annot.vertices or []
                        if words is None:
                            words = page.get_text("words", sort=True)
                        selected = []
                        for i in range(0, len(vertices) - 3, 4):
                            quad = fitz.Quad(vertices[i : i + 4])
                            for word in words:
                                center = fitz.Point(
                                    (word[0] + word[2]) / 2,
                                    (word[1] + word[3]) / 2,
                                )
                                if center in quad and word not in selected:
                                    selected.append(word)
                        text = " ".join(word[4] for word in selected)
                    if not text and not content:
                        if kind not in {
                            "Highlight",
                            "Underline",
                            "StrikeOut",
                            "Squiggly",
                        }:
                            continue
                        text = "[No selectable text found]"
                    count += 1
                    url = f"{pdf_path.resolve().as_uri()}#page={page_number}"
                    lines.append(f"### {kind} — [page {page_number}]({url})")
                    lines.append("")
                    if text:
                        lines.extend(f"> {line}" for line in text.splitlines())
                        lines.append("")
                    if content:
                        lines.extend(f"> {line}" for line in content.splitlines())
                        lines.append("")
        if not count:
            lines.extend(["_No PDF annotations found._", ""])
        lines.append(self._ANNOTATIONS_END)
        return "\n".join(lines)

    def _merge_annotations(self, content: str, section: str) -> str:
        """Replace the managed section while preserving all other note content."""
        starts = list(
            re.finditer(
                r"(?m)^" + re.escape(self._ANNOTATIONS_START) + r"(?=\r?$)", content
            )
        )
        ends = list(
            re.finditer(
                r"(?m)^" + re.escape(self._ANNOTATIONS_END) + r"(?=\r?$)", content
            )
        )
        if not starts and not ends:
            empty = (
                f"{self._ANNOTATIONS_START}\n## PDF annotations\n\n"
                f"_No PDF annotations found._\n\n{self._ANNOTATIONS_END}"
            )
            if section == empty:
                return content
            separator = (
                "" if not content else ("\n" if content.endswith("\n") else "\n\n")
            )
            return content + separator + section + "\n"
        if len(starts) != 1 or len(ends) != 1 or ends[0].start() < starts[0].start():
            raise ValueError("Invalid PDF annotation section markers in note.")
        return content[: starts[0].start()] + section + content[ends[0].end() :]

    def generate(
        self,
        directory: Path,
        state: Any,
        notes_dir: Path | None = None,
        apply: bool = False,
        force: bool = False,
    ) -> list[NotePlan]:
        """Plan (and, if `apply`, write) Obsidian notes for every tracked PDF."""
        try:
            import fitz  # noqa: F401
        except ImportError as exc:
            raise ValueError(
                "Exporting PDF annotations requires PyMuPDF. "
                "Install adsfolio with the full extra."
            ) from exc
        directory = Path(directory)
        target_dir = Path(notes_dir) if notes_dir is not None else directory

        records: dict[str, dict[str, Any]] = dict(state.files)
        plans: list[NotePlan] = []
        sections: dict[str, str] = {}
        existing: dict[str, str] = {}
        for original_name, metadata in records.items():
            current_name = str(metadata.get("current_filename", original_name))
            pdf_path = directory / current_name
            note_filename = Path(current_name).with_suffix(".md").name
            note_path = target_dir / note_filename

            bibcode = self._first(metadata.get("bibcode"))
            title = self._first(metadata.get("title"))
            if not bibcode or not title:
                action = "skip-unresolved"
            elif not pdf_path.exists():
                action = "skip-missing-file"
            else:
                try:
                    section = self._annotation_section(pdf_path)
                    sections[original_name] = section
                    if note_path.exists():
                        content = note_path.read_bytes().decode("utf-8")
                        existing[original_name] = content
                        candidate = self._merge_annotations(content, section)
                        if force:
                            candidate = self._merge_annotations(
                                self.render_note(metadata, pdf_path), section
                            )
                        action = "skip-unchanged" if candidate == content else "update"
                    else:
                        action = "create"
                except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
                    self.logger.warning(
                        "Cannot export annotations from %s: %s", pdf_path, exc
                    )
                    action = "skip-error"

            plans.append(
                NotePlan(original_name, note_filename, note_path, pdf_path, action)
            )

        if not apply:
            return plans

        writable = [plan for plan in plans if plan.action in ("create", "update")]
        missing_bibcodes = [
            self._first(records[plan.original_filename].get("bibcode"))
            for plan in writable
            if (plan.action == "create" or force)
            and not records[plan.original_filename].get("abstract")
        ]
        abstracts = (
            self.fetch_missing_abstracts(missing_bibcodes) if missing_bibcodes else {}
        )

        changed = False
        for plan in writable:
            metadata = records[plan.original_filename]
            bibcode = self._first(metadata.get("bibcode"))
            abstract = abstracts.get(bibcode)
            if abstract:
                metadata = dict(metadata)
                metadata["abstract"] = abstract
                state.upsert_file(plan.original_filename, metadata)
                records[plan.original_filename] = metadata
                changed = True

            if plan.original_filename in existing and not force:
                content = existing[plan.original_filename]
            else:
                content = self.render_note(metadata, plan.pdf_path)
            content = self._merge_annotations(content, sections[plan.original_filename])
            if content == existing.get(plan.original_filename):
                plan.action = "skip-unchanged"
                continue
            if plan.note_path.exists():
                if (
                    plan.original_filename not in existing
                    or plan.note_path.read_bytes()
                    != existing[plan.original_filename].encode("utf-8")
                ):
                    self.logger.warning(
                        "Note changed during export: %s", plan.note_path
                    )
                    plan.action = "skip-changed"
                    continue
            elif plan.original_filename in existing:
                self.logger.warning("Note removed during export: %s", plan.note_path)
                plan.action = "skip-changed"
                continue
            plan.note_path.parent.mkdir(parents=True, exist_ok=True)
            plan.note_path.write_bytes(content.encode("utf-8"))

        if changed:
            state.save()

        return plans
