# Start with your PDF library

Install a [desktop download](https://github.com/ysBach/adsfolio/releases/latest).
Python is included. See [desktop installation](desktop.md) for platform limits
and unsigned-app approval.

## Add your existing folders

1. Open ADSfolio.
2. Choose **File → Add Library Root…**.
3. Select your PDF folder.
4. Enable **Include Subdirectories** if your papers use nested folders.
5. Press **Refresh** or **F5** to scan the selected root.

Adding a root records file locations. It does not move your PDFs.
You can browse your library without an ADS account.
Start with one folder before processing a large collection.

## Identify papers in a batch

1. Select your library root.
2. Open **Identify**.
3. Choose the scan scope and provider order.
4. Start identification.
5. Review the results and job log.

Identification checks saved results before online lookups.
The default provider order is ADS, Crossref, then DataCite.
Crossref and DataCite look up exact DOIs without an ADS token.
ADS title searches require your token.
Set it through **Tools → NASA ADS Token…** when requested.

Processing time depends on PDF text, cached results, provider limits, and your
connection. Scanned PDFs may need external OCR first. ADSfolio does not bundle OCR.
OpenAlex and PubMed lookup are not supported.

## Review metadata before renaming

A bibcode identifies a publication. It does not prove that the current PDF
matches that publication. Stored citation fields can remain available while
file verification is pending. Inspect the status tooltip for the reason.

1. Select a paper and inspect its metadata.
2. Correct fields when necessary.
3. Open **Rename**.
4. Review the filename preview.
5. Apply the changes you accept.

Papers with sufficient metadata can be renamed without an ADS bibcode.
Use **File → Local References…** for manual entries or BibTeX imports.
See [references outside ADS](local-references.md).

## Keep the library current

Press **F5** after moving or deleting files outside ADSfolio.
Local refresh reconciles file facts. ADS refresh retrieves remote metadata.
Permanent cleanup is separate.

The catalog stores metadata, file locations, and version history outside the app.
Replacing the application does not delete that catalog.
Close the earlier application before first-use migration.
Keep its original data until you check the migrated library.

See the [application guide](gui.md) for settings and folder watching.
See [CLI and Python workflows](command-line-and-python.md) for scripts and APIs.
