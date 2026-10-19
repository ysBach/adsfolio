CLI Reference
=============

PDF workflow state is stored in the central user catalog `pdf-library.sqlite`;
use `adsfolio pdf export` for readable snapshots. Legacy per-root state is read
without modification during migration. The user publication cache is managed by `adsfolio cache status`,
`adsfolio cache refresh`, and `adsfolio cache clear`. Concurrent writes from two machines
against the same library are unsupported; keep export snapshots as backups.

.. click:: adsfolio.cli.commands:main
   :prog: adsfolio
   :nested: full
