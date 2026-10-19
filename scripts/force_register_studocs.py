import csv
from pathlib import Path
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.identifier import PdfIdentifier

csv_path = Path("/Users/ysbach/Dropbox/Studocs/resolved_ai.csv")
target_dir = Path("/Users/ysbach/Dropbox/Studocs")

state = open_registry_state(target_dir)
ident = PdfIdentifier(client=None)

with csv_path.open("r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        rel_path = row.get("relative_path", "").strip()
        if not rel_path:
            continue
        pdf_file = target_dir / rel_path
        if not pdf_file.exists():
            print(f"Skipping missing file: {rel_path}")
            continue

        bibcode = row.get("ads_bibcode", "").strip()
        title = row.get("title", "").strip()
        year = row.get("year", "").strip()
        bibstem = row.get("bibstem", "").strip()
        vol = row.get("vol", "").strip()
        page = row.get("page", "").strip()

        authors = []
        for i in range(1, 5):
            a = row.get(f"author{i}", "").strip()
            if a:
                authors.append(a)

        doc = {
            "bibcode": bibcode
            if bibcode
            else (
                f"{year}{bibstem}..{vol}..{page}" if year and bibstem else "UNRESOLVED"
            ),
            "title": [title] if title else [pdf_file.stem],
            "author": authors,
            "year": year,
            "bibstem": bibstem,
            "volume": vol,
            "page": page,
        }

        sha256 = ident.compute_sha256(pdf_file)
        record = ident.build_record(doc, pdf_file, sha256=sha256)
        state.upsert_file(rel_path, record)
        print(f"Registered: {rel_path} -> bibcode: {record.get('bibcode')}")

state.save()
print("Successfully populated sidecar database!")
