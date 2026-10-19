from pathlib import Path
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.renamer import PdfRenamer
from adsfolio.pdf.identifier import PdfIdentifier

publocr_dir = Path("/Users/ysbach/Dropbox/Studocs/PublOCR")
unresolved_dir = publocr_dir / "unresolved"

state = open_registry_state(publocr_dir)
ident = PdfIdentifier(client=None)
renamer = PdfRenamer(client=None)

# Map sha256 to sidecar record
records = list(state.registry.iter_files())
sha_map = {rec["sha256"]: rec for rec in records if rec.get("sha256")}

unresolved_files = list(unresolved_dir.glob("*.pdf"))

moved_count = 0
for p in unresolved_files:
    sha = ident.compute_sha256(p)
    rec = sha_map.get(sha)
    if not rec:
        continue

    meta = rec.get("metadata", {})
    if not meta.get("bibcode"):
        continue

    new_name = renamer.render(
        "{bibcode}_{authors}_{title}",
        meta,
        author_format="surname_initials",
        max_authors=1,
        char_policy="ascii",
    )

    dest_path = publocr_dir / new_name
    if dest_path.exists() and dest_path != p:
        print(f"Destination exists: {new_name}, skipping")
        continue

    print(f"Moving: {p.name}\n     -> {new_name}")
    p.rename(dest_path)
    state.set_current_filename(rec["relative_path"], new_name)
    moved_count += 1

state.save()
print(f"\nSuccessfully moved and renamed {moved_count} resolved files into PublOCR/")
