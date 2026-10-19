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
    "CATALOG_FILENAME",
    "REGISTRY_FILENAME",
    "SCHEMA_VERSION",
    "AnnotationRecord",
    "MatchedAnnotation",
    "PdfCatalog",
    "PdfIdentifier",
    "PdfRegistry",
    "PdfRenamer",
    "PdfUpgrader",
    "PdfWatcher",
    "PortingReport",
    "Sidecar",
    "apply_annotations",
    "extract_annotations",
    "match_annotations",
    "port_annotations",
]
