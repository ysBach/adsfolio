CLI Reference
=============

PDF workflow state is stored in the central user catalog `pdf-library.sqlite`;
use `adsapi pdf export` for readable snapshots. Legacy per-root state is read
without modification during migration. The user publication cache is managed by `adsapi cache status`,
`adsapi cache refresh`, and `adsapi cache clear`. Concurrent writes from two machines
against the same library are unsupported; keep export snapshots as backups.

.. click:: adsapi.cli.commands:main
   :prog: adsapi
   :nested: full
