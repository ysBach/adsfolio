{{ fullname | escape | underline }}

.. automodule:: {{ fullname }}
{%- if fullname == "adsfolio.pdf.registry" %}
   :members: CentralPdfRegistry, CentralPdfRegistryState, FileReconciliation, merge_ads_metadata, migrate_sidecar, open_registry_state
   :ignore-module-all:

``PdfRegistry`` is the compatibility alias of
:class:`~adsfolio.pdf.registry.CentralPdfRegistry`; ``PdfRegistryState`` aliases
:class:`~adsfolio.pdf.registry.CentralPdfRegistryState`.
``REGISTRY_FILENAME`` names the legacy per-root database. Use
:data:`adsfolio.pdf.catalog.CATALOG_FILENAME` for the central catalog filename.
{%- elif fullname == "adsfolio.pdf.tracked_move" %}
   :members: TrackedMoveRollbackError, TrackedFileState, move_tracked_file
   :ignore-module-all:
{%- elif fullname == "adsfolio.gui.watch" %}
   :members: LibraryWatchController, WatchFailure, _WorkerProtocol
   :ignore-module-all:
   :private-members: _WorkerProtocol
{%- elif fullname == "adsfolio.pdf.catalog" %}
   :exclude-members: CATALOG_FILENAME

.. autodata:: adsfolio.pdf.catalog.CATALOG_FILENAME
{%- elif fullname in ["adsfolio", "adsfolio.api", "adsfolio.bibtex", "adsfolio.pdf"] %}
   :no-members:

See the implementation modules listed in the :doc:`API index <../index>` for
class and function details. Package-level imports are convenience re-exports.
{%- endif %}
