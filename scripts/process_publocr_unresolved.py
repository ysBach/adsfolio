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

# Journal abbreviation mapping to ADS bibstem (5 chars)
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
    "ME SCT": "MeScT",
    "MESCT": "MeScT",
    "PHYZ": "PhyZ.",
}


def clean_author_name(raw: str) -> str:
    cleaned = (
        unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    )
    cleaned = re.sub(r"[^A-Za-z\s\,]", "", cleaned).strip()
    return cleaned


def parse_row(row):
    rel_path = row["relative_path"]
    fname = Path(rel_path).name
    text = row.get("first_page_text", "")
    pdf_path = unresolved_dir / fname

    if not pdf_path.exists():
        return None

    # 1. Exact bibcode in filename or text
    bib = ident.extract_bibcode_from_filename(fname) or ident.extract_bibcode_from_text(
        text
    )
    if not bib:
        m = BIBCODE_PATTERN.search(fname)
        if m:
            bib = m.group(1)

    if bib:
        bib = bib.replace("+", "&")  # Normalize ampersand in bibstem e.g. A+A -> A&A
        return {
            "path": pdf_path,
            "rel_path": rel_path,
            "bibcode": bib,
            "title": pdf_path.stem,
            "author": [],
            "source": "exact",
        }

    # 2. Filename pattern parsing
    # e.g. Author+2005_Sci_310_258_Title
    # e.g. Author_2011_BaltA_20_363_Title
    p1 = re.search(r"^(.+?)[_\+](\d{4})_([A-Za-z0-9&]+)_([A-Za-z0-9]+)_(\d+)_", fname)
    if p1:
        author_raw, year, journal_raw, vol, page = p1.groups()
        j_upper = journal_raw.upper()
        bibstem = JOURNAL_MAP.get(j_upper, journal_raw.ljust(5, ".")[:5])

        v_str = vol.rjust(4, ".")[-4:]
        p_str = page.rjust(4, ".")[-4:]

        first_author_raw = author_raw.split("+")[0].split("-")[0].split("_")[0]
        first_author_clean = clean_author_name(first_author_raw)
        first_char = first_author_clean[0].upper() if first_author_clean else "A"

        # Check if letter page or electronic
        letter = "L" if "L" in j_upper or "LETT" in j_upper else "."

        constructed_bib = f"{year}{bibstem}{v_str}{letter}{p_str}{first_char}"

        # Extract title if present in filename after page
        title_part = fname.split(f"_{page}_", 1)[-1]
        title_part = re.sub(r"_\d+_\d+_\d+_\d+_\d+_\d+\.pdf$", "", title_part)
        title_part = re.sub(r"\.pdf$", "", title_part).replace("_", " ")

        return {
            "path": pdf_path,
            "rel_path": rel_path,
            "bibcode": constructed_bib,
            "title": title_part or pdf_path.stem,
            "author": [first_author_clean],
            "year": year,
            "bibstem": bibstem,
            "volume": vol,
            "page": page,
            "source": "constructed",
        }

    # 3. DOI candidate
    meta_doi, text_doi = extract_doi_candidates_pdf(pdf_path)
    doi = meta_doi or text_doi or ident.extract_doi_from_filename(fname)
    if doi:
        return {
            "path": pdf_path,
            "rel_path": rel_path,
            "doi": doi,
            "title": pdf_path.stem,
            "author": [],
            "source": "doi",
        }

    return None


def main():
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Total unresolved CSV rows: {len(rows)}")

    results = []
    for r in rows:
        parsed = parse_row(r)
        if parsed:
            results.append(parsed)

    print(f"Successfully parsed metadata for {len(results)} / {len(rows)} files.")

    moved_count = 0
    skipped_count = 0

    for item in results:
        pdf_path = item["path"]
        rel_path = item["rel_path"]
        bibcode = item.get("bibcode")
        if not bibcode:
            continue

        sha256 = ident.compute_sha256(pdf_path)
        doc = {
            "bibcode": bibcode,
            "title": [item.get("title", pdf_path.stem)],
            "author": item.get("author", []),
            "year": item.get("year", ""),
            "bibstem": item.get("bibstem", ""),
            "volume": item.get("volume", ""),
            "page": item.get("page", ""),
        }
        record = ident.build_record(doc, pdf_path, sha256=sha256)
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
        f"Finished! Successfully moved & renamed {moved_count} files into PublOCR/ ({skipped_count} skipped existing)."
    )


if __name__ == "__main__":
    main()
