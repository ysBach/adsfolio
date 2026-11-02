Command-line (CLI) reference
============================

PDF workflow state is stored in the central user catalog `pdf-library.sqlite`;
use `adsfolio pdf export` for readable snapshots. Legacy per-root state is read
without modification during migration. The user publication cache is managed by `adsfolio cache status`,
`adsfolio cache refresh`, and `adsfolio cache clear`. Concurrent writes from two machines
against the same library are unsupported; keep export snapshots as backups.

Reference hints search ADS and must agree with identifiers from the selected
PDF. Recovery can use that PDF's own DOI or arXiv ID with enabled providers.
Use ``pdf refresh-ads --identify-scope global`` to select global provider settings.

``pdf rename`` reports each unchanged or skipped filename.
It uses the exact selected template without substituting another template.
Missing metadata fields render as empty components.

``pdf identify`` automatically fetches an unverified saved ADS candidate when ADS
is enabled and available. Matching PDF identifiers establish verification without
manual review. Valid saved proofs and cached provider records avoid that request.
The GUI forwards its selected scope automatically.

Identify preserves existing CSV reports and AI input files. A new report receives
a numeric suffix when its default filename already exists. Use the printed path.

.. click:: adsfolio.cli.commands:main
   :prog: adsfolio
   :nested: full
