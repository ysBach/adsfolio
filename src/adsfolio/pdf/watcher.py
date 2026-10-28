"""PDF filesystem watcher with polling and optional watchdog backends."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from sqlite3 import Error as SQLiteError
from typing import Any

from ..config import ADSError
from .cli_common import is_resolved_record
from .doi_providers import DEFAULT_DOI_PROVIDERS, DoiProvider, ProviderError
from .files import iter_pdf_paths
from .identifier import PdfIdentifier
from .identify import build_cached_candidate_lookup
from .identify_settings import (
    RECOMMENDED_IDENTIFY_STRATEGIES,
    IdentifySettings,
    IdentifyStrategy,
)
from .identity import IDENTITY_KEY, IdentityValidationError, UnverifiedIdentityError
from .readiness import WATCH_PENDING_KEY, metadata_ready
from .registry import CentralPdfRegistryState
from .rename_settings import RenameSettings
from .renamer import DEFAULT_SCHEME, PdfRenamer, verify_record_identity
from .sidecar import Sidecar
from .tracked_move import (
    TrackedMoveConflictError,
    move_tracked_file,
    resolved_file_directory,
)
from .upgrade import PdfUpgrader
from .watch_session import WatchEvent, WatchOutcome, WatchState

__all__ = ["PdfNotReadyError", "PdfWatcher", "WatchIdentifyPolicy"]


class PdfNotReadyError(RuntimeError):
    """Indicate that a PDF should be retried after its filesystem state changes."""


@dataclass(frozen=True)
class WatchIdentifyPolicy:
    """Resolved single-file Identify path policy for one watched root."""

    root: Path
    unresolved_directory: Path
    skip_unresolved_directory: bool

    @classmethod
    def from_settings(
        cls,
        root: Path,
        settings: IdentifySettings,
    ) -> WatchIdentifyPolicy:
        """Resolve output and skip containment from reusable settings.

        Parameters
        ----------
        root
            Watched library root.
        settings
            Effective Identify settings for the watched root.

        Returns
        -------
        WatchIdentifyPolicy
            Normalized root, unresolved directory, and skip behavior. Relative
            output paths are rooted under the library; an unset output uses
            `<root>/unresolved` for skip containment, matching bulk Identify.

        Raises
        ------
        ValueError
            If skipping is enabled and the unresolved directory contains the
            watched root.
        """
        normalized_root = Path(root).expanduser().resolve()
        configured = settings.output_unresolved
        if configured:
            output = Path(configured).expanduser()
            unresolved = (
                output if output.is_absolute() else normalized_root / output
            ).resolve()
        else:
            unresolved = (normalized_root / "unresolved").resolve()
        if settings.skip_unresolved_directory:
            try:
                normalized_root.relative_to(unresolved)
            except ValueError:
                pass
            else:
                raise ValueError(
                    "Cannot skip the unresolved directory because it contains "
                    "the watched root."
                )
        return cls(
            root=normalized_root,
            unresolved_directory=unresolved,
            skip_unresolved_directory=settings.skip_unresolved_directory,
        )

    def excludes(self, path: Path) -> bool:
        """Return whether `path` is inside the configured skip directory."""
        if not self.skip_unresolved_directory:
            return False
        try:
            Path(path).expanduser().resolve().relative_to(self.unresolved_directory)
        except ValueError:
            return False
        return True


class PdfWatcher:
    """Watch directories for new PDF files and process them."""

    def __init__(
        self,
        identifier: PdfIdentifier,
        renamer: PdfRenamer,
        upgrader: PdfUpgrader | None = None,
    ) -> None:
        self.identifier = identifier
        self.renamer = renamer
        self.upgrader = upgrader
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def _relative_path(root: Path, path: Path) -> str:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError(f"PDF is outside watched root: {path}") from exc

    def _unique_destination(self, output_dir: Path, filename: str) -> Path:
        destination = output_dir / filename
        if not destination.exists():
            return destination

        stem = Path(filename).stem
        suffix = Path(filename).suffix
        counter = 1
        while True:
            candidate = output_dir / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _move_unresolved(
        self,
        pdf_path: Path,
        sidecar: Sidecar,
        output_unresolved: Path | None,
        apply: bool,
    ) -> Path:
        if output_unresolved is None:
            self.logger.info("Unresolved PDF left in place: %s", pdf_path)
            return pdf_path
        if pdf_path.parent.resolve() == output_unresolved.resolve():
            self.logger.info("Unresolved PDF already in output directory: %s", pdf_path)
            return pdf_path
        if not apply:
            self.logger.info(
                "Dry-run unresolved move: %s -> %s", pdf_path, output_unresolved
            )
            return self._unique_destination(output_unresolved, pdf_path.name)

        output_unresolved.mkdir(parents=True, exist_ok=True)
        target = self._unique_destination(output_unresolved, pdf_path.name)
        move_tracked_file(sidecar, pdf_path, target)
        self.logger.info("Moved unresolved PDF: %s -> %s", pdf_path, target)
        return target

    @staticmethod
    def _file_facts(path: Path) -> tuple[int, int, int, int]:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino

    def _ready_sha256(self, path: Path) -> str:
        try:
            facts_before = self._file_facts(path)
            if not self.identifier.pdf_is_readable(path):
                raise PdfNotReadyError(f"PDF is not readable yet: {path}")
            sha256 = self.identifier.compute_sha256(path)
            facts_after = self._file_facts(path)
        except OSError as exc:
            raise PdfNotReadyError(
                f"PDF filesystem state is not stable: {path}"
            ) from exc
        if facts_before != facts_after:
            raise PdfNotReadyError(f"PDF changed while it was being inspected: {path}")
        return sha256

    def process_new_pdf(
        self,
        pdf_path: Path,
        sidecar: Sidecar,
        scheme: str = DEFAULT_SCHEME,
        author_format: str = "surname_initials",
        char_policy: str = "keep",
        max_authors: int = 3,
        authors_when_truncated: int = 1,
        output_unresolved: Path | None = None,
        apply: bool = True,
        journal_map: dict | None = None,
        strategies: tuple[IdentifyStrategy, ...] = RECOMMENDED_IDENTIFY_STRATEGIES,
        providers: tuple[DoiProvider, ...] = DEFAULT_DOI_PROVIDERS,
        author_separator: str = "+",
        author_truncation_suffix: str = "+",
    ) -> dict | None:
        """Identify and rename a single PDF, returning resolved metadata.

        Every stored record is checked against the PDF's own identity before
        the catalog is written or the file is renamed, including a dry run.

        Raises
        ------
        IdentityValidationError
            If a stored or newly found identity conflicts with the PDF or
            cannot be independently verified. Nothing is written or moved.
        """
        path = Path(pdf_path)
        if path.suffix.lower() != ".pdf" or not path.exists():
            return None
        root = Path(sidecar.directory).resolve()
        relative_path = self._relative_path(root, path)

        sha256 = self._ready_sha256(path)
        target_directory = resolved_file_directory(root, path, output_unresolved)
        rename_options = {
            "template": scheme,
            "dry_run": not apply,
            "author_format": author_format,
            "max_authors": max_authors,
            "authors_when_truncated": authors_when_truncated,
            "char_policy": char_policy,
            "author_separator": author_separator,
            "author_truncation_suffix": author_truncation_suffix,
            "target_directory": target_directory,
        }
        existing = sidecar.find_by_current_filename(relative_path)
        if existing is not None:
            _current_path, record = existing
            if str(record.get("sha256") or "") == sha256:
                refreshed = dict(record)
                refreshed["current_filename"] = relative_path
                refreshed["sha256"] = sha256
                refreshed[WATCH_PENDING_KEY] = False
                if is_resolved_record(refreshed):
                    refreshed[IDENTITY_KEY] = verify_record_identity(
                        path, refreshed, sha256=sha256, state=sidecar
                    )
                    if apply and isinstance(sidecar, CentralPdfRegistryState):
                        refreshed = sidecar.upsert_file(_current_path, refreshed)
                    target = self.renamer.rename_tracked_file(
                        state=sidecar,
                        source_path=path,
                        metadata=refreshed,
                        **rename_options,
                    )
                    if apply:
                        refreshed["current_filename"] = self._relative_path(
                            root, target
                        )
                        if target == path:
                            sidecar.upsert_file(_current_path, refreshed)
                            sidecar.save()
                    elif target != path:
                        refreshed["planned_filename"] = self._relative_path(
                            root, target
                        )
                        self.logger.info(
                            "Dry-run resolved rename: %s -> %s", path, target
                        )
                    return refreshed

        existing = sidecar.find_by_sha256(sha256)
        if existing is not None and is_resolved_record(existing[1]):
            record = dict(existing[1])
            verify_record_identity(path, record, sha256=sha256, state=sidecar)
        else:
            record = self.identifier.resolve_many(
                [path],
                journal_map=journal_map,
                strategies=strategies,
                providers=providers,
                sha256_by_path={path: sha256},
                cached_candidate_lookup=(
                    build_cached_candidate_lookup(self.identifier, sidecar.registry, [])
                    if isinstance(sidecar, CentralPdfRegistryState)
                    else None
                ),
            ).get(path)
            # Identifiers without failure tracking report no failures.
            failure = getattr(self.identifier, "identity_failures", {}).get(path)
            if failure is not None:
                raise failure
        if record is None:
            if apply:
                sidecar.upsert_file(
                    relative_path,
                    {
                        "current_filename": relative_path,
                        "sha256": sha256,
                        "status": "unresolved",
                        WATCH_PENDING_KEY: False,
                    },
                )
                sidecar.save()
            self._move_unresolved(path, sidecar, output_unresolved, apply)
            return None

        stored_record = dict(record)
        stored_record["current_filename"] = relative_path
        stored_record["sha256"] = sha256
        stored_record[WATCH_PENDING_KEY] = False
        stored_record[IDENTITY_KEY] = verify_record_identity(
            path, stored_record, sha256=sha256, state=sidecar
        )
        if existing is not None:
            self.logger.info("Registering another location for tracked PDF: %s", path)
        if apply:
            persisted = sidecar.upsert_file(relative_path, stored_record)
            if isinstance(persisted, dict):
                stored_record = persisted
            sidecar.save()
        if not (is_resolved_record(stored_record) or metadata_ready(stored_record)):
            return stored_record
        target = self.renamer.rename_tracked_file(
            state=sidecar,
            source_path=path,
            metadata=stored_record,
            **rename_options,
        )
        if apply:
            stored_record["current_filename"] = self._relative_path(root, target)
        elif target != path:
            stored_record["planned_filename"] = self._relative_path(root, target)
            self.logger.info("Dry-run resolved rename: %s -> %s", path, target)
        return stored_record

    def process_event(
        self,
        event: WatchEvent,
        state: Sidecar | CentralPdfRegistryState,
        rename_settings: RenameSettings,
        identify_settings: IdentifySettings,
    ) -> WatchOutcome:
        """Process one typed watch event using reusable GUI settings.

        Parameters
        ----------
        event : WatchEvent
            Detected filesystem event to process.
        state : Sidecar or CentralPdfRegistryState
            Root-scoped catalog state owned by the caller's worker thread.
        rename_settings : RenameSettings
            Reusable filename rendering settings.
        identify_settings : IdentifySettings
            Reusable identification settings.

        Returns
        -------
        WatchOutcome
            Typed processing result. Readiness failures are deferred and
            expected processing failures are reported as failed outcomes.
        """
        source = Path(event.path).resolve()
        try:
            policy = WatchIdentifyPolicy.from_settings(
                Path(state.directory), identify_settings
            )
            if policy.excludes(source):
                return WatchOutcome(
                    event=replace(event, state=WatchState.DISMISSED),
                    destination=source,
                    message="Ignored PDF in the unresolved directory",
                )
            unresolved_destination: Path | None = None
            if identify_settings.output_unresolved:
                unresolved_destination = self._unique_destination(
                    policy.unresolved_directory, source.name
                )
            record = self.process_new_pdf(
                pdf_path=source,
                sidecar=state,
                scheme=rename_settings.template,
                author_format=rename_settings.author_format,
                char_policy=rename_settings.char_policy,
                max_authors=rename_settings.max_authors,
                authors_when_truncated=rename_settings.authors_when_truncated,
                author_separator=rename_settings.author_separator,
                author_truncation_suffix=rename_settings.author_truncation_suffix,
                output_unresolved=(
                    unresolved_destination.parent
                    if unresolved_destination is not None
                    else None
                ),
                apply=True,
                strategies=identify_settings.strategies,
                providers=identify_settings.providers,
            )
        except IdentityValidationError as exc:
            return WatchOutcome(
                event=replace(event, state=WatchState.FAILED),
                message=str(exc),
                candidate_bibcode=(
                    exc.candidate_bibcode
                    if isinstance(exc, UnverifiedIdentityError)
                    else None
                ),
                candidate_sha256=(
                    exc.candidate_sha256
                    if isinstance(exc, UnverifiedIdentityError)
                    else None
                ),
                candidate_fingerprint=(
                    exc.candidate_fingerprint
                    if isinstance(exc, UnverifiedIdentityError)
                    else None
                ),
                candidate_confirmable=(
                    isinstance(exc, UnverifiedIdentityError)
                    and exc.manually_confirmable
                ),
            )
        except TrackedMoveConflictError as exc:
            return WatchOutcome(
                event=replace(event, state=WatchState.FAILED),
                destination=source,
                conflict_path=exc.destination,
                message=str(exc),
            )
        except PdfNotReadyError as exc:
            return WatchOutcome(
                event=replace(event, state=WatchState.DEFERRED),
                message=str(exc),
            )
        except (ADSError, ProviderError, OSError, SQLiteError, ValueError) as exc:
            return WatchOutcome(
                event=replace(event, state=WatchState.FAILED),
                message=str(exc),
            )

        if record is None:
            destination = (
                unresolved_destination
                if unresolved_destination is not None and not source.exists()
                else source
            )
            return WatchOutcome(
                event=replace(event, state=WatchState.UNRESOLVED),
                destination=destination,
                message="No ADS record identified",
            )

        current_filename = Path(str(record.get("current_filename", source.name)))
        destination = (
            current_filename
            if current_filename.is_absolute()
            else Path(state.directory) / current_filename
        ).resolve()
        outcome_state = (
            WatchState.RESOLVED if destination == source else WatchState.RENAMED
        )
        bibcode = record.get("bibcode")
        return WatchOutcome(
            event=replace(event, state=outcome_state),
            destination=destination,
            bibcode=str(bibcode) if bibcode else None,
        )

    @staticmethod
    def _catalog_facts(record: dict[str, Any]) -> tuple[int, int, int, int]:
        return (
            int(record["size_bytes"]),
            int(record["mtime_ns"]),
            int(record.get("device") or 0),
            int(record.get("inode") or 0),
        )

    def _changed_pdf_paths(
        self,
        directory: Path,
        sidecar: Sidecar,
        recursive: bool,
        last_facts: dict[str, tuple[int, int, int, int]],
    ) -> tuple[list[Path], set[str]]:
        """Return changed PDFs and the relative paths seen in a complete scan.

        Raises
        ------
        OSError
            If a directory cannot be listed or a listed file cannot be examined
            for a reason other than having disappeared. The scan is then
            incomplete, so callers must not mark unseen files missing.
        """
        changed: list[Path] = []
        seen_paths: set[str] = set()
        registry = getattr(sidecar, "registry", None)
        for pdf_path in iter_pdf_paths(directory, recursive):
            try:
                relative_path = self._relative_path(directory, pdf_path)
                facts = self._file_facts(pdf_path)
            except ValueError:
                # A symbolic link to a file outside the watched root is not
                # part of the library.
                self.logger.debug("Ignoring PDF outside watched root: %s", pdf_path)
                continue
            except FileNotFoundError:
                continue
            seen_paths.add(relative_path)
            existing = (
                registry.find_file_by_current_path(directory, relative_path)
                if registry is not None
                else None
            )
            if existing is not None and facts == self._catalog_facts(existing):
                continue
            if last_facts.get(relative_path) != facts:
                changed.append(pdf_path)
        return changed, seen_paths

    def _scan_or_none(
        self,
        directory: Path,
        sidecar: Sidecar,
        recursive: bool,
        last_facts: dict[str, tuple[int, int, int, int]],
    ) -> tuple[list[Path], set[str]] | None:
        """Scan once, returning `None` and logging if the scan was incomplete."""
        try:
            return self._changed_pdf_paths(directory, sidecar, recursive, last_facts)
        except OSError as exc:
            self.logger.error(
                "Incomplete scan of %s; keeping tracked files unchanged: %s",
                directory,
                exc,
            )
            return None

    def _process_changed_path(
        self,
        directory: Path,
        sidecar: Sidecar,
        path: Path,
        handler: Callable[[Path], Any],
        last_facts: dict[str, tuple[int, int, int, int]],
    ) -> None:
        try:
            facts = self._file_facts(path)
            relative_path = self._relative_path(directory, path)
        except (OSError, ValueError):
            return
        if last_facts.get(relative_path) == facts:
            return
        registry = getattr(sidecar, "registry", None)
        existing = (
            registry.find_file_by_current_path(directory, relative_path)
            if registry is not None
            else None
        )
        if existing is not None and facts == self._catalog_facts(existing):
            return
        try:
            handler(path)
        except PdfNotReadyError as exc:
            self.logger.info("Deferring PDF until it is ready: %s", exc)
            return
        except (
            ADSError,
            ProviderError,
            OSError,
            RuntimeError,
            SQLiteError,
            ValueError,
        ) as exc:
            self.logger.error("Failed processing watched PDF %s: %s", path, exc)
            last_facts[relative_path] = facts
            return
        last_facts[relative_path] = facts

    def poll_loop(
        self,
        directory: Path,
        sidecar: Sidecar,
        interval: float = 10.0,
        recursive: bool = False,
        callback: Callable[[Path], Any] | None = None,
        stop_event: Any | None = None,
        auto_upgrade: bool = False,
        upgrade_interval_hours: float = 24.0,
        upgrade_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Poll filesystem for new PDFs and process them."""
        directory = Path(directory)
        handler = callback or (
            lambda path: self.process_new_pdf(pdf_path=path, sidecar=sidecar)
        )
        last_upgrade_check = 0.0
        last_facts: dict[str, tuple[int, int, int, int]] = {}

        while True:
            scan = self._scan_or_none(directory, sidecar, recursive, last_facts)
            if scan is not None:
                changed_paths, seen_paths = scan
                for pdf_path in changed_paths:
                    self._process_changed_path(
                        directory, sidecar, pdf_path, handler, last_facts
                    )

                registry = getattr(sidecar, "registry", None)
                if registry is not None:
                    registry.catalog.mark_missing_locations(
                        directory, seen_paths, recursive=recursive
                    )

            if auto_upgrade:
                elapsed = time.time() - last_upgrade_check
                if elapsed >= max(1.0, upgrade_interval_hours * 3600):
                    self._run_auto_upgrade(sidecar, upgrade_kwargs or {})
                    last_upgrade_check = time.time()

            if stop_event is not None and stop_event.is_set():
                return
            time.sleep(interval)

    def watchdog_loop(
        self,
        directory: Path,
        sidecar: Sidecar,
        recursive: bool = False,
        callback: Callable[[Path], Any] | None = None,
        reconciliation_interval: float = 10.0,
        stop_event: Any | None = None,
        auto_upgrade: bool = False,
        upgrade_interval_hours: float = 24.0,
        upgrade_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Use watchdog Observer backend to react to filesystem events."""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError as exc:
            raise RuntimeError(
                "watchdog is required for --use-watchdog. Install adsfolio[full]."
            ) from exc

        directory = Path(directory)
        handler = callback or (
            lambda path: self.process_new_pdf(pdf_path=path, sidecar=sidecar)
        )
        pending_paths: queue.Queue[Path] = queue.Queue()
        queued_paths: set[Path] = set()
        queue_lock = threading.Lock()
        last_facts: dict[str, tuple[int, int, int, int]] = {}

        def enqueue(path: Path) -> None:
            candidate = Path(path)
            if candidate.suffix.lower() != ".pdf" or candidate.name.startswith("."):
                return
            resolved = candidate.resolve()
            with queue_lock:
                if resolved in queued_paths:
                    return
                queued_paths.add(resolved)
                pending_paths.put(resolved)

        class PdfEventHandler(FileSystemEventHandler):
            def on_created(self, event) -> None:
                if event.is_directory:
                    return
                path = Path(event.src_path)
                enqueue(path)

            def on_moved(self, event) -> None:
                if event.is_directory:
                    return
                path = Path(event.dest_path)
                enqueue(path)

            def on_modified(self, event) -> None:
                if not event.is_directory:
                    enqueue(Path(event.src_path))

        observer = Observer()
        observer.schedule(PdfEventHandler(), str(directory), recursive=recursive)
        observer.start()
        last_upgrade_check = 0.0
        last_reconciliation = 0.0
        try:
            while True:
                now = time.monotonic()
                if now - last_reconciliation >= reconciliation_interval:
                    scan = self._scan_or_none(directory, sidecar, recursive, last_facts)
                    if scan is not None:
                        changed_paths, seen_paths = scan
                        for pdf_path in changed_paths:
                            enqueue(pdf_path)
                        registry = getattr(sidecar, "registry", None)
                        if registry is not None:
                            registry.catalog.mark_missing_locations(
                                directory, seen_paths, recursive=recursive
                            )
                    last_reconciliation = now
                try:
                    path = pending_paths.get(timeout=0.5)
                except queue.Empty:
                    path = None
                if path is not None:
                    try:
                        self._process_changed_path(
                            directory, sidecar, path, handler, last_facts
                        )
                    finally:
                        with queue_lock:
                            queued_paths.discard(path)
                if auto_upgrade:
                    elapsed = time.time() - last_upgrade_check
                    if elapsed >= max(1.0, upgrade_interval_hours * 3600):
                        self._run_auto_upgrade(sidecar, upgrade_kwargs or {})
                        last_upgrade_check = time.time()
                if stop_event is not None and stop_event.is_set():
                    return
        finally:
            observer.stop()
            observer.join()

    def _run_auto_upgrade(
        self, sidecar: Sidecar, upgrade_kwargs: dict[str, Any]
    ) -> None:
        if self.upgrader is None:
            self.logger.warning(
                "Auto-upgrade requested but no PdfUpgrader is configured."
            )
            return

        results = self.upgrader.batch_upgrade(sidecar=sidecar, **upgrade_kwargs)
        upgraded = [result for result in results if result.status == "upgraded"]
        if upgraded:
            self.logger.info("Auto-upgrade updated %d PDF(s).", len(upgraded))
