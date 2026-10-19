import re
import csv
import unicodedata
from pathlib import Path
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.renamer import PdfRenamer
from adsfolio.pdf.identifier import (
    BIBCODE_PATTERN,
    PdfIdentifier,
    extract_doi_candidates_pdf,
)

publocr_dir = Path("/Users/ysbach/Dropbox/Studocs/PublOCR")
unresolved_dir = publocr_dir / "unresolved"
csv_path = unresolved_dir / "unresolved.csv"

state = open_registry_state(publocr_dir)
ident = PdfIdentifier(client=None)
renamer = PdfRenamer(client=None)

JOURNAL_MAP = {
    "APJ": "ApJ..",
    "APJL": "ApJ..",
    "AJ": "AJ...",
    "MNRAS": "MNRAS",
    "A&A": "A&A..",
    "AA": "A&A..",
    "A+A": "A&A..",
    "A&AS": "A&AS.",
    "A+AS": "A&AS.",
    "PASP": "PASP.",
    "PASJ": "PASJ.",
    "ICAR": "Icar.",
    "ICARUS": "Icar.",
    "SCI": "Sci..",
    "SCIENCE": "Sci..",
    "SCIA": "SciA.",
    "BALTA": "BaltA",
    "NATUR": "Natur",
    "NATURE": "Natur",
    "LPSC": "LPSC.",
    "LPI": "LPI..",
    "GEORL": "GeoRL",
    "GRL": "GeoRL",
    "JGR": "JGR..",
    "JASA": "JASA.",
    "SPIE": "SPIE.",
    "MPBU": "MPBu.",
    "JKAS": "JKAS.",
    "RAA": "RAA..",
    "AISM": "AISM.",
    "STPAP": "StPap",
    "STCOM": "StCom",
    "BMET": "BMet.",
    "SIAMR": "SIAMR",
    "JGCD": "JGCD.",
    "JOSA": "JOSA.",
    "APOPT": "ApOpt",
    "MESCT": "MeScT",
    "PHYZ": "PhyZ.",
    "JAPA": "JApA.",
    "SVAL": "SvAL.",
    "SASS": "SASS.",
    "MNPS": "MnPS.",
}


def clean_author_name(raw: str) -> str:
    cleaned = (
        unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    )
    cleaned = re.sub(r"[^A-Za-z\s\,]", "", cleaned).strip()
    return cleaned


def parse_filename_additional(fname, text):
    # Pattern A: AuthorYearJournal_Vol_Page or Author+YearJournal_Vol_Page
    mA = re.search(
        r"^(.+?)[_\+]?(\d{4})([A-Za-z]+)[_\=]?(\d+)[_\=](\d+[A-Za-z]?)", fname
    )
    if mA:
        author_raw, year, journal_raw, vol, page = mA.groups()
        j_upper = journal_raw.upper()
        bibstem = JOURNAL_MAP.get(j_upper, journal_raw.ljust(5, ".")[:5])

        letter = "."
        page_num = page
        if page[-1].isalpha():
            letter = page[-1].upper()
            page_num = page[:-1]
        elif "L" in j_upper or "LETT" in j_upper:
            letter = "L"

        v_str = vol.rjust(4, ".")[-4:]
        p_str = page_num.rjust(4, ".")[-4:]

        first_author_raw = author_raw.split("+")[0].split("-")[0].split("_")[0]
        first_author_clean = clean_author_name(first_author_raw)
        first_char = first_author_clean[0].upper() if first_author_clean else "A"

        constructed_bib = f"{year}{bibstem}{v_str}{letter}{p_str}{first_char}"
        title_part = fname.split(f"_{page}_", 1)[-1] if f"_{page}_" in fname else fname
        title_part = re.sub(r"_\d+_\d+_\d+_\d+_\d+_\d+\.pdf$", "", title_part).replace(
            "_", " "
        )

        return {
            "bibcode": constructed_bib,
            "title": title_part or fname,
            "author": [first_author_clean],
            "year": year,
            "bibstem": bibstem,
            "volume": vol,
            "page": page,
            "source": "constructed_A",
        }

    # Pattern B: Author_et_al._-_Year_-_Title
    mB = re.search(r"^(.+?)_et_al\._-__?(\d{4})_-?_(.+)\.pdf$", fname)
    if mB:
        author_raw, year, title_raw = mB.groups()
        first_author_clean = clean_author_name(author_raw.replace("_", " "))
        first_char = first_author_clean[0].upper() if first_author_clean else "A"
        title_clean = title_raw.replace("_", " ")

        arxiv_match = re.search(r"(\d{4}\.\d{4,5})", text or fname)
        if arxiv_match:
            ar_id = arxiv_match.group(1).replace(".", "")
            bibcode = f"{year}arXiv{ar_id}{first_char}"
        else:
            bibcode = f"{year}arXiv....{first_char}"

        return {
            "bibcode": bibcode,
            "title": title_clean,
            "author": [first_author_clean],
            "year": year,
            "bibstem": "arXiv",
            "source": "constructed_B",
        }

    return None


def main():
    unresolved_files = list(unresolved_dir.glob("*.pdf"))
    print(f"Total PDFs remaining in unresolved: {len(unresolved_files)}")

    results = []
    for pdf_path in unresolved_files:
        fname = pdf_path.name
        rel_path = fname

        # Check existing sidecar records first
        sha256 = ident.compute_sha256(pdf_path)
        existing = state.find_by_sha256(sha256)
        if (
            existing
            and existing[1].get("bibcode")
            and existing[1].get("bibcode") != "UNRESOLVED"
        ):
            results.append((pdf_path, rel_path, existing[1]))
            continue

        try:
            text = ident._first_page_text(pdf_path)
        except Exception:
            text = ""

        parsed = parse_filename_additional(fname, text)
        if parsed:
            doc = {
                "bibcode": parsed["bibcode"],
                "title": [parsed.get("title", pdf_path.stem)],
                "author": parsed.get("author", []),
                "year": parsed.get("year", ""),
                "bibstem": parsed.get("bibstem", ""),
                "volume": parsed.get("volume", ""),
                "page": parsed.get("page", ""),
            }
            record = ident.build_record(doc, pdf_path, sha256=sha256)
            results.append((pdf_path, rel_path, record))

    print(f"Additional parsed files: {len(results)}")

    moved_count = 0
    skipped_count = 0

    for pdf_path, rel_path, record in results:
        state.upsert_file(rel_path, record)

        new_name = renamer.render(
            "{bibcode}_{authors}_{title}",
            record,
            author_format="surname_initials",
            max_authors=1,
            char_policy="ascii",
        )

        dest_path = publocr_dir / new_name
        if dest_path.exists() and dest_path != pdf_path:
            skipped_count += 1
            continue

        pdf_path.rename(dest_path)
        state.set_current_filename(rel_path, new_name)
        moved_count += 1

    state.save()
    print(
        f"Done! Moved & renamed {moved_count} additional files into PublOCR/ ({skipped_count} skipped)."
    )


if __name__ == "__main__":
    main()
