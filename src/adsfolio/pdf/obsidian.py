"""Obsidian-friendly Markdown note generation for tracked PDFs."""

import logging
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

    def generate(
        self,
        directory: Path,
        state: Any,
        notes_dir: Path | None = None,
        apply: bool = False,
        force: bool = False,
    ) -> list[NotePlan]:
        """Plan (and, if `apply`, write) Obsidian notes for every tracked PDF."""
        directory = Path(directory)
        target_dir = Path(notes_dir) if notes_dir is not None else directory

        records: dict[str, dict[str, Any]] = dict(state.files)
        plans: list[NotePlan] = []
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
            elif note_path.exists() and not force:
                action = "skip-exists"
            elif note_path.exists():
                action = "update"
            else:
                action = "create"

            plans.append(
                NotePlan(original_name, note_filename, note_path, pdf_path, action)
            )

        if not apply:
            return plans

        writable = [plan for plan in plans if plan.action in ("create", "update")]
        missing_bibcodes = [
            self._first(records[plan.original_filename].get("bibcode"))
            for plan in writable
            if not records[plan.original_filename].get("abstract")
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

            plan.note_path.parent.mkdir(parents=True, exist_ok=True)
            content = self.render_note(metadata, plan.pdf_path)
            plan.note_path.write_text(content, encoding="utf-8")

        if changed:
            state.save()

        return plans
