"""PDF tooling for ADS metadata identification and organization."""

from .annotations import (
    AnnotationRecord,
    MatchedAnnotation,
    PortingReport,
    apply_annotations,
    extract_annotations,
    match_annotations,
    port_annotations,
)
from .catalog import CATALOG_FILENAME, PdfCatalog
from .identifier import PdfIdentifier
from .registry import REGISTRY_FILENAME, SCHEMA_VERSION, PdfRegistry
from .renamer import PdfRenamer
from .sidecar import Sidecar
from .upgrade import PdfUpgrader
from .watcher import PdfWatcher

__all__ = [
    "AnnotationRecord",
    "MatchedAnnotation",
    "PortingReport",
    "extract_annotations",
    "match_annotations",
    "apply_annotations",
    "port_annotations",
    "PdfIdentifier",
    "PdfCatalog",
    "CATALOG_FILENAME",
    "PdfRegistry",
    "REGISTRY_FILENAME",
    "SCHEMA_VERSION",
    "PdfRenamer",
    "Sidecar",
    "PdfUpgrader",
    "PdfWatcher",
]
