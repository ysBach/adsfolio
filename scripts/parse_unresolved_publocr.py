import re
import csv
from pathlib import Path
from adsapi.pdf.identifier import BIBCODE_PATTERN, PdfIdentifier

csv_path = Path("/Users/ysbach/Dropbox/Studocs/PublOCR/unresolved/unresolved.csv")
unresolved_dir = Path("/Users/ysbach/Dropbox/Studocs/PublOCR/unresolved")

ident = PdfIdentifier(client=None)

exact_bibcode_count = 0
parsed_pattern_count = 0
doi_count = 0
unmatched_count = 0

records = []

with csv_path.open("r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        rel_path = row["relative_path"]
        fname = Path(rel_path).name
        text = row.get("first_page_text", "")

        # 1. Check if bibcode in text or filename
        bib = ident.extract_bibcode_from_filename(
            fname
        ) or ident.extract_bibcode_from_text(text)
        if not bib:
            m = BIBCODE_PATTERN.search(fname)
            if m:
                bib = m.group(1)

        if bib:
            exact_bibcode_count += 1
            records.append((rel_path, bib, "exact", row))
            continue

        # 2. Try regex pattern on filename (e.g. Author_Year_Journal_Vol_Page_Title)
        # e.g. A'HearnMF+2005_Sci_310_258_Deep Impact...
        # e.g. AfanasievVL+MoiseevAV_2011_BaltA_20_363_Scorpio...
        pattern1 = re.search(
            r"^(.+?)[_\+](\d{4})_([A-Za-z0-9]+)_([A-Za-z0-9]+)_(\d+)_", fname
        )
        if pattern1:
            author_str, year, journal, vol, page = pattern1.groups()
            first_author_surname = author_str.split("+")[0].split("-")[0].split("_")[0]
            first_author_clean = re.sub(r"[^A-Za-z]", "", first_author_surname)
            first_char = first_author_clean[0].upper() if first_author_clean else "A"

            j_stem = journal.ljust(5, ".")[:5]
            v_str = vol.rjust(4, ".")[-4:]
            p_str = page.rjust(4, ".")[-4:]
            constructed_bib = f"{year}{j_stem}{v_str}.{p_str}{first_char}"
            parsed_pattern_count += 1
            records.append((rel_path, constructed_bib, "constructed", row))
            continue

        # 3. Check for DOI in text or filename
        doi = ident.extract_doi_from_filename(fname) or ident._extract_doi(text)
        if doi:
            doi_count += 1
            records.append((rel_path, doi, "doi", row))
            continue

        unmatched_count += 1

print(
    f"Total entries: {exact_bibcode_count + parsed_pattern_count + doi_count + unmatched_count}"
)
print(f"Exact bibcode found: {exact_bibcode_count}")
print(f"Constructed from filename pattern: {parsed_pattern_count}")
print(f"DOI found: {doi_count}")
print(f"Unmatched: {unmatched_count}")

print("\nSample Exact Bibcodes:")
for r in [x for x in records if x[2] == "exact"][:5]:
    print(" ", r[0], "->", r[1])

print("\nSample Constructed Bibcodes:")
for r in [x for x in records if x[2] == "constructed"][:5]:
    print(" ", r[0], "->", r[1])

print("\nSample DOIs:")
for r in [x for x in records if x[2] == "doi"][:5]:
    print(" ", r[0], "->", r[1])
