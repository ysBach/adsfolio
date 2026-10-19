Python API Reference
====================

This reference is generated from the current checkout. The GUI guide and CLI
reference describe user workflows; the modules below expose the corresponding
Python interfaces. Explicit module lists avoid importing the executable
``adsfolio.cli.__main__`` entry point during documentation discovery.

Client, configuration, and formatting
-------------------------------------

.. autosummary::
   :toctree: generated

   adsfolio
   adsfolio.client
   adsfolio.config
   adsfolio.data_migration
   adsfolio.user_profile
   adsfolio.pubcache
   adsfolio.citations
   adsfolio.formatting
   adsfolio.journal_utils
   adsfolio.library_sync

ADS services and BibTeX
-----------------------

.. autosummary::
   :toctree: generated

   adsfolio.api
   adsfolio.api.search
   adsfolio.api.export
   adsfolio.api.libraries
   adsfolio.api.metrics
   adsfolio.api.journals
   adsfolio.bibtex
   adsfolio.bibtex.parser
   adsfolio.bibtex.formatter
   adsfolio.bibtex.io
   adsfolio.bibtex.sanitize
   adsfolio.references

Publication lists and CLI
-------------------------

.. autosummary::
   :toctree: generated

   adsfolio.cli
   adsfolio.cli.commands
   adsfolio.reference_cli
   adsfolio.cv
   adsfolio.cv.config
   adsfolio.cv.config_starter
   adsfolio.cv.query
   adsfolio.cv.renderer
   adsfolio.cv.render_factory
   adsfolio.cv.render_legend
   adsfolio.cv.cli

PDF catalog and processing
--------------------------

.. autosummary::
   :toctree: generated

   adsfolio.pdf
   adsfolio.pdf.sidecar
   adsfolio.pdf.catalog
   adsfolio.pdf.registry
   adsfolio.pdf.cli
   adsfolio.pdf.cli_common
   adsfolio.pdf.cli_upgrade
   adsfolio.pdf.arxiv
   adsfolio.pdf.files
   adsfolio.pdf.identifier
   adsfolio.pdf.doi_providers
   adsfolio.pdf.readiness
   adsfolio.pdf.identify
   adsfolio.pdf.identify_settings
   adsfolio.pdf.rename_settings
   adsfolio.pdf.renamer
   adsfolio.pdf.watcher
   adsfolio.pdf.watch_session
   adsfolio.pdf.tracked_move
   adsfolio.pdf.prefetch
   adsfolio.pdf.obsidian
   adsfolio.pdf.annotations
   adsfolio.pdf.annotation_merge
   adsfolio.pdf.upgrade
   adsfolio.pdf.upgrade_utils

Desktop GUI
-----------

These modules require the ``gui`` extra (PySide6). Importing them does not
launch a window. See :doc:`../gui` for operation and :doc:`../desktop` for
standalone bundle setup.

.. autosummary::
   :toctree: generated

   adsfolio.gui
   adsfolio.gui.app
   adsfolio.gui.config
   adsfolio.gui.library
   adsfolio.gui.icons
   adsfolio.gui.models
   adsfolio.gui.main_window
   adsfolio.gui.detail_pane
   adsfolio.gui.annotation_merge
   adsfolio.gui.dialogs
   adsfolio.gui.appearance
   adsfolio.gui.citation_styles
   adsfolio.gui.ads_library_dialog
   adsfolio.gui.ads_library_export_dialog
   adsfolio.gui.references_dialog
   adsfolio.gui.ads_library_jobs
   adsfolio.gui.jobs
   adsfolio.gui.refresh_job
   adsfolio.gui.token_dialog
   adsfolio.gui.notifications
   adsfolio.gui.osint
   adsfolio.gui.watch
