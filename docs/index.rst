ADSfolio documentation
======================

This guide covers ADSfolio |release|.

ADSfolio (package adsfolio) is a scientific literature workflow suite,
desktop PDF library browser, and Python/CLI client for NASA ADS / SciXplorer
and connected bibliographic discovery services.

ADSfolio uses the official `NASA ADS API <https://github.com/adsabs/adsabs-dev-api>`_
for searches, citation exports, metrics, ADS libraries, and journal information.
We thank the ADS team for providing these services and their API documentation.
ADSfolio is an independent project, not an official ADS application.

Where to start:

- **New to the desktop app:** read :doc:`getting-started`, then
  :doc:`workflows`.
- **Writing scripts or using a terminal:** start at :doc:`command-line-and-python`
  and follow the tutorials in order.
- **Looking up a command or function:** use the API reference.

.. toctree::
   :maxdepth: 2
   :caption: Desktop app

   getting-started
   workflows
   gui
   local-references

.. toctree::
   :maxdepth: 2
   :caption: CLI and Python

   command-line-and-python
   cli-tutorials/01-setup-and-search
   cli-tutorials/02-export-and-bibtex
   cli-tutorials/03-library-workflows
   cli-tutorials/04-pdf-workflows
   cli-tutorials/05-operations-and-troubleshooting
   cli-tutorials/06-cv-workflows
   query-to-document-example

.. toctree::
   :maxdepth: 1
   :caption: API reference

   api/index
   cli
   cv

.. toctree::
   :maxdepth: 1
   :caption: Project

   maintaining
   desktop
