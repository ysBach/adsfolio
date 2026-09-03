import re
import csv
from pathlib import Path

csv_path = Path("/Users/ysbach/Dropbox/Studocs/PublOCR/unresolved/unresolved.csv")

unmatched = []
with csv_path.open("r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        rel_path = row["relative_path"]
        fname = Path(rel_path).name

        # Check if year in filename
        m = re.search(r"(\d{4})", fname)
        if m:
            unmatched.append((fname, m.group(1)))
        else:
            unmatched.append((fname, "no_year"))

print(f"Total unmatched count sample: {len(unmatched)}")
for fn, yr in unmatched[:30]:
    print(f"  [{yr}] {fn}")
