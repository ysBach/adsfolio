"""Pure Python watch session state machine and immutable event types."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

__all__ = [
    "ArrivalIdentity",
    "FileSnapshot",
    "WatchEvent",
    "WatchOutcome",
    "WatchSession",
    "WatchSource",
    "WatchState",
]

logger = logging.getLogger(__name__)


class WatchState(str, Enum):
    """Watch item processing states."""

    DETECTED = "detected"
    PROCESSING = "processing"
    DEFERRED = "deferred"
    DISMISSED = "dismissed"
    RENAMED = "renamed"
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    STALE = "stale"
    FAILED = "failed"


class WatchSource(str, Enum):
    """Origin of a watch observation."""

    CREATED = "created"
    MODIFIED = "modified"
    MOVED_IN = "moved_in"
    RECONCILE = "reconcile"


@dataclass(frozen=True)
class FileSnapshot:
    """Point-in-time filesystem facts for a single file."""

    path: Path
    size_bytes: int
    mtime_ns: int
    device: int
    inode: int
    birth_time_ns: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            object.__setattr__(self, "path", Path(self.path))

    @classmethod
    def from_stat(cls, path: Path | str, st: os.stat_result) -> FileSnapshot:
        """Create a snapshot from an existing os.stat_result.

        Parameters
        ----------
        path : Path or str
            Filesystem path of the file.
        st : os.stat_result
            Stat result from os.stat.

        Returns
        -------
        FileSnapshot
            Constructed immutable snapshot.
        """
        birth_ns = getattr(st, "st_birthtime_ns", None)
        if birth_ns is None and hasattr(st, "st_birthtime"):
            birth_ns = int(st.st_birthtime * 1_000_000_000)
        return cls(
            path=Path(path),
            size_bytes=st.st_size,
            mtime_ns=st.st_mtime_ns,
            device=st.st_dev,
            inode=st.st_ino,
            birth_time_ns=birth_ns,
        )

    @classmethod
    def from_path(cls, path: Path | str) -> FileSnapshot:
        """Create a snapshot by calling stat() on the path.

        Parameters
        ----------
        path : Path or str
            Filesystem path to inspect.

        Returns
        -------
        FileSnapshot
            Constructed immutable snapshot.
        """
        p = Path(path)
        return cls.from_stat(p, p.stat())


@dataclass(frozen=True)
class ArrivalIdentity:
    """Stable identity for an arrival, distinguishing inode recycling."""

    device: int
    inode: int
    birth_time_ns: int | None
    generation: int


@dataclass(frozen=True)
class WatchEvent:
    """Immutable watch notification payload."""

    root: Path
    session_id: str
    identity: ArrivalIdentity
    path: Path
    state: WatchState

    def __post_init__(self) -> None:
        if not isinstance(self.root, Path):
            object.__setattr__(self, "root", Path(self.root))
        if not isinstance(self.path, Path):
            object.__setattr__(self, "path", Path(self.path))


@dataclass(frozen=True)
class WatchOutcome:
    """Final or intermediate processing result of a watched event."""

    event: WatchEvent
    destination: Path | None = None
    bibcode: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if self.destination is not None and not isinstance(self.destination, Path):
            object.__setattr__(self, "destination", Path(self.destination))


class WatchSession:
    """Manage watch baseline exclusion and per-arrival lifecycle transitions."""

    def __init__(
        self,
        root: Path | str,
        session_id: str,
        baseline: Iterable[FileSnapshot] = (),
    ) -> None:
        self.root = Path(root).resolve()
        self.session_id = str(session_id)
        self._baseline: set[ArrivalIdentity] = set()
        self._physical_baseline: set[tuple[int, int, int | None]] = set()
        self._retired_physical_keys: set[tuple[int, int, int | None]] = set()
        self._missing_while_processing: set[tuple[int, int, int | None]] = set()
        self._generation_map: dict[tuple[int, int, int | None], int] = {}
        self._snapshots: dict[ArrivalIdentity, FileSnapshot] = {}
        self._states: dict[ArrivalIdentity, WatchState] = {}
        self._active_by_key: dict[tuple[int, int, int | None], ArrivalIdentity] = {}
        self._arrivals_during_starting: set[tuple[int, int, int | None]] = set()
        self._starting: bool = False
        self._sequence: int = 0
        self._last_scan_token: int | None = None
        self._created_at_seq: dict[ArrivalIdentity, int] = {}
        self._baseline_created_at_seq: dict[tuple[int, int, int | None], int] = {}
        if baseline:
            self.finish_baseline(baseline)

    @classmethod
    def starting(cls, root: Path | str, session_id: str) -> WatchSession:
        """Construct a session in the starting state before baseline enumeration.

        Parameters
        ----------
        root : Path or str
            Watched directory root.
        session_id : str
            Unique session identifier.

        Returns
        -------
        WatchSession
            Starting watch session.
        """
        session = cls(root, session_id, baseline=())
        session._starting = True
        return session

    @property
    def baseline(self) -> frozenset[ArrivalIdentity]:
        """Return immutable set of baseline arrival identities."""
        return frozenset(self._baseline)

    def start_scan(self) -> int:
        """Record the start of a baseline or reconciliation scan.

        Returns
        -------
        int
            Monotonic scan token.
        """
        token = self._sequence
        self._last_scan_token = token
        return token

    def finish_baseline(self, snapshots: Iterable[FileSnapshot]) -> None:
        """Populate baseline physical identities after intake activation.

        Parameters
        ----------
        snapshots : Iterable[FileSnapshot]
            Snapshots discovered during initial directory scan.
        """
        for s in snapshots:
            key = (s.device, s.inode, s.birth_time_ns)
            if key in self._arrivals_during_starting:
                continue
            self._physical_baseline.add(key)
            self._baseline_created_at_seq[key] = self._sequence
            if key not in self._generation_map:
                self._generation_map[key] = 0
            self._baseline.add(
                ArrivalIdentity(
                    device=s.device,
                    inode=s.inode,
                    birth_time_ns=s.birth_time_ns,
                    generation=0,
                )
            )
        self._starting = False

    def snapshot(self, identity: ArrivalIdentity) -> FileSnapshot | None:
        """Return the last known snapshot for an identity.

        Parameters
        ----------
        identity : ArrivalIdentity
            Arrival identity to look up.

        Returns
        -------
        FileSnapshot or None
            Snapshot if tracked, otherwise None.
        """
        return self._snapshots.get(identity)

    def state(self, identity: ArrivalIdentity) -> WatchState | None:
        """Return the current state for an identity.

        Parameters
        ----------
        identity : ArrivalIdentity
            Arrival identity to look up.

        Returns
        -------
        WatchState or None
            Current state if tracked, otherwise None.
        """
        return self._states.get(identity)

    def is_baseline(self, snapshot_or_identity: FileSnapshot | ArrivalIdentity) -> bool:
        """Check if a snapshot or identity belongs to the baseline.

        Parameters
        ----------
        snapshot_or_identity : FileSnapshot or ArrivalIdentity
            Object to check.

        Returns
        -------
        bool
            True if in baseline, False otherwise.
        """
        if isinstance(snapshot_or_identity, ArrivalIdentity):
            return snapshot_or_identity in self._baseline
        key = (
            snapshot_or_identity.device,
            snapshot_or_identity.inode,
            snapshot_or_identity.birth_time_ns,
        )
        return key in self._physical_baseline

    def observe(self, snapshot: FileSnapshot, source: WatchSource) -> WatchEvent | None:
        """Process a filesystem observation and return an event when eligible.

        Parameters
        ----------
        snapshot : FileSnapshot
            Current filesystem facts.
        source : WatchSource
            Origin of observation.

        Returns
        -------
        WatchEvent or None
            Emitted event when a state transition occurs, otherwise None.
        """
        key = (snapshot.device, snapshot.inode, snapshot.birth_time_ns)

        if source in (WatchSource.CREATED, WatchSource.MOVED_IN):
            if key in self._active_by_key:
                active_id = self._active_by_key[key]
                active_state = self._states[active_id]
                if active_state != WatchState.STALE:
                    active_snap = self._snapshots[active_id]
                    facts_changed = active_state == WatchState.DEFERRED and (
                        snapshot.size_bytes != active_snap.size_bytes
                        or snapshot.mtime_ns != active_snap.mtime_ns
                    )
                    self._snapshots[active_id] = snapshot
                    self._missing_while_processing.discard(key)
                    if active_state == WatchState.DEFERRED:
                        if facts_changed:
                            self._states[active_id] = WatchState.DETECTED
                            return WatchEvent(
                                root=self.root,
                                session_id=self.session_id,
                                identity=active_id,
                                path=snapshot.path,
                                state=WatchState.DETECTED,
                            )
                        return None
                    return None

            self._sequence += 1
            if self._starting:
                self._arrivals_during_starting.add(key)
            if key in self._generation_map:
                gen = self._generation_map[key] + 1
            else:
                gen = 0
            self._generation_map[key] = gen
            identity = ArrivalIdentity(
                device=snapshot.device,
                inode=snapshot.inode,
                birth_time_ns=snapshot.birth_time_ns,
                generation=gen,
            )
            if key in self._active_by_key:
                old_id = self._active_by_key[key]
                if old_id != identity:
                    self._states[old_id] = WatchState.STALE
            self._snapshots[identity] = snapshot
            self._states[identity] = WatchState.DETECTED
            self._active_by_key[key] = identity
            self._created_at_seq[identity] = self._sequence
            self._retired_physical_keys.discard(key)
            self._missing_while_processing.discard(key)
            return WatchEvent(
                root=self.root,
                session_id=self.session_id,
                identity=identity,
                path=snapshot.path,
                state=WatchState.DETECTED,
            )

        if source == WatchSource.MODIFIED:
            if key not in self._active_by_key:
                return None
            identity = self._active_by_key[key]
            current_state = self._states[identity]
            self._missing_while_processing.discard(key)
            if current_state == WatchState.DEFERRED:
                last_snap = self._snapshots[identity]
                facts_changed = (
                    snapshot.size_bytes != last_snap.size_bytes
                    or snapshot.mtime_ns != last_snap.mtime_ns
                    or snapshot.path != last_snap.path
                )
                if facts_changed:
                    self._states[identity] = WatchState.DETECTED
                    self._snapshots[identity] = snapshot
                    return WatchEvent(
                        root=self.root,
                        session_id=self.session_id,
                        identity=identity,
                        path=snapshot.path,
                        state=WatchState.DETECTED,
                    )
                return None
            if current_state == WatchState.DETECTED:
                self._snapshots[identity] = snapshot
                return None
            return None

        if source == WatchSource.RECONCILE:
            if key in self._physical_baseline:
                return None
            if key in self._active_by_key:
                identity = self._active_by_key[key]
                self._missing_while_processing.discard(key)
                current_state = self._states[identity]
                last_snap = self._snapshots[identity]
                facts_changed = current_state == WatchState.DEFERRED and (
                    snapshot.size_bytes != last_snap.size_bytes
                    or snapshot.mtime_ns != last_snap.mtime_ns
                    or snapshot.path != last_snap.path
                )
                self._snapshots[identity] = snapshot
                if current_state == WatchState.DEFERRED:
                    if facts_changed:
                        self._states[identity] = WatchState.DETECTED
                        return WatchEvent(
                            root=self.root,
                            session_id=self.session_id,
                            identity=identity,
                            path=snapshot.path,
                            state=WatchState.DETECTED,
                        )
                return None

            self._sequence += 1
            gen = self._generation_map.get(key, 0)
            self._generation_map[key] = gen
            identity = ArrivalIdentity(
                device=snapshot.device,
                inode=snapshot.inode,
                birth_time_ns=snapshot.birth_time_ns,
                generation=gen,
            )
            self._snapshots[identity] = snapshot
            self._states[identity] = WatchState.DETECTED
            self._active_by_key[key] = identity
            self._created_at_seq[identity] = self._sequence
            return WatchEvent(
                root=self.root,
                session_id=self.session_id,
                identity=identity,
                path=snapshot.path,
                state=WatchState.DETECTED,
            )

        return None

    def reconcile(
        self,
        current_snapshots: Iterable[FileSnapshot],
        *,
        scan_token: int | None = None,
    ) -> list[WatchEvent]:
        """Perform a full-snapshot reconciliation against currently present files.

        Parameters
        ----------
        current_snapshots : Iterable[FileSnapshot]
            All files currently present in the watched directory.
        scan_token : int or None, optional
            Scan token from start_scan(). If None, defaults to any active scan token
            or current sequence.

        Returns
        -------
        list of WatchEvent
            Events emitted for new or woken-up arrivals during reconciliation.
        """
        token = (
            scan_token
            if scan_token is not None
            else (
                self._last_scan_token
                if self._last_scan_token is not None
                else self._sequence
            )
        )
        self._last_scan_token = None
        snapshots_list = list(current_snapshots)
        present_keys = {(s.device, s.inode, s.birth_time_ns) for s in snapshots_list}
        events: list[WatchEvent] = []

        for key in list(self._physical_baseline):
            if key not in present_keys:
                base_seq = self._baseline_created_at_seq.get(key, 0)
                if base_seq <= token:
                    self._physical_baseline.remove(key)
                    self._retired_physical_keys.add(key)
                    self._baseline = {
                        b
                        for b in self._baseline
                        if (b.device, b.inode, b.birth_time_ns) != key
                    }

        for key, identity in list(self._active_by_key.items()):
            if key not in present_keys:
                current_state = self._states.get(identity)
                created_seq = self._created_at_seq.get(identity, 0)
                if created_seq > token:
                    continue
                if current_state == WatchState.PROCESSING:
                    self._missing_while_processing.add(key)
                    continue
                if current_state not in (
                    WatchState.RENAMED,
                    WatchState.RESOLVED,
                    WatchState.UNRESOLVED,
                    WatchState.DISMISSED,
                ):
                    self._states[identity] = WatchState.STALE
                self._retired_physical_keys.add(key)
                del self._active_by_key[key]

        for snapshot in snapshots_list:
            key = (snapshot.device, snapshot.inode, snapshot.birth_time_ns)
            if key in self._physical_baseline:
                continue
            if key in self._active_by_key:
                identity = self._active_by_key[key]
                self._missing_while_processing.discard(key)
                current_state = self._states[identity]
                last_snap = self._snapshots[identity]
                facts_changed = current_state == WatchState.DEFERRED and (
                    snapshot.size_bytes != last_snap.size_bytes
                    or snapshot.mtime_ns != last_snap.mtime_ns
                    or snapshot.path != last_snap.path
                )
                self._snapshots[identity] = snapshot
                if current_state == WatchState.DEFERRED:
                    if facts_changed:
                        self._states[identity] = WatchState.DETECTED
                        events.append(
                            WatchEvent(
                                root=self.root,
                                session_id=self.session_id,
                                identity=identity,
                                path=snapshot.path,
                                state=WatchState.DETECTED,
                            )
                        )
                continue

            self._sequence += 1
            if key in self._generation_map:
                gen = self._generation_map[key] + 1
            else:
                gen = 0
            self._generation_map[key] = gen
            identity = ArrivalIdentity(
                device=snapshot.device,
                inode=snapshot.inode,
                birth_time_ns=snapshot.birth_time_ns,
                generation=gen,
            )
            self._snapshots[identity] = snapshot
            self._states[identity] = WatchState.DETECTED
            self._active_by_key[key] = identity
            self._created_at_seq[identity] = self._sequence
            self._retired_physical_keys.discard(key)
            self._missing_while_processing.discard(key)
            events.append(
                WatchEvent(
                    root=self.root,
                    session_id=self.session_id,
                    identity=identity,
                    path=snapshot.path,
                    state=WatchState.DETECTED,
                )
            )

        return events

    def _validate_current_identity(self, identity: ArrivalIdentity) -> WatchState:
        if identity not in self._states:
            raise ValueError(f"Unknown or stale identity: {identity}")
        current = self._states[identity]
        key = (identity.device, identity.inode, identity.birth_time_ns)
        if self._active_by_key.get(key) != identity:
            raise ValueError(
                f"Cannot perform transition on stale or superseded identity: {identity}"
            )
        return current

    def begin_processing(self, identity: ArrivalIdentity) -> WatchEvent:
        """Transition a detected arrival into the processing state.

        Parameters
        ----------
        identity : ArrivalIdentity
            Arrival to begin processing.

        Returns
        -------
        WatchEvent
            Event in PROCESSING state.

        Raises
        ------
        ValueError
            If identity is unknown, stale, or not in DETECTED state.
        """
        current = self._validate_current_identity(identity)
        if current == WatchState.PROCESSING:
            raise ValueError(f"Identity is already being processed: {identity}")
        if current != WatchState.DETECTED:
            raise ValueError(
                f"Cannot begin processing identity in state {current}: {identity}"
            )
        self._states[identity] = WatchState.PROCESSING
        return WatchEvent(
            root=self.root,
            session_id=self.session_id,
            identity=identity,
            path=self._snapshots[identity].path,
            state=WatchState.PROCESSING,
        )

    def complete(self, identity: ArrivalIdentity, state: WatchState) -> WatchEvent:
        """Record the outcome state of an arrival.

        Parameters
        ----------
        identity : ArrivalIdentity
            Arrival identity.
        state : WatchState
            Target outcome state.

        Returns
        -------
        WatchEvent
            Event in target outcome state.

        Raises
        ------
        ValueError
            If identity is unknown/stale or transition is invalid.
        """
        current = self._validate_current_identity(identity)
        if state in (WatchState.DETECTED, WatchState.PROCESSING):
            raise ValueError(f"Invalid completion state: {state}")
        if current in (
            WatchState.DISMISSED,
            WatchState.RENAMED,
            WatchState.RESOLVED,
            WatchState.UNRESOLVED,
            WatchState.STALE,
        ):
            raise ValueError(
                f"Cannot complete identity from terminal state {current}: {identity}"
            )
        if current == WatchState.FAILED:
            raise ValueError(
                f"Cannot complete failed identity without retry: {identity}"
            )
        self._states[identity] = state
        event = WatchEvent(
            root=self.root,
            session_id=self.session_id,
            identity=identity,
            path=self._snapshots[identity].path,
            state=state,
        )
        key = (identity.device, identity.inode, identity.birth_time_ns)
        if key in self._missing_while_processing:
            self._retired_physical_keys.add(key)
            self._missing_while_processing.discard(key)
            del self._active_by_key[key]
        return event

    def dismiss(self, identity: ArrivalIdentity) -> WatchEvent:
        """Mark an arrival as dismissed for this session.

        Parameters
        ----------
        identity : ArrivalIdentity
            Arrival identity.

        Returns
        -------
        WatchEvent
            Event in DISMISSED state.

        Raises
        ------
        ValueError
            If identity is unknown/stale, currently processing, or already in a terminal state.
        """
        current = self._validate_current_identity(identity)
        if current == WatchState.PROCESSING:
            raise ValueError(
                f"Cannot dismiss identity while it is being processed: {identity}"
            )
        if current in (
            WatchState.RENAMED,
            WatchState.RESOLVED,
            WatchState.UNRESOLVED,
            WatchState.STALE,
        ):
            raise ValueError(
                f"Cannot dismiss identity from terminal state {current}: {identity}"
            )
        self._states[identity] = WatchState.DISMISSED
        return WatchEvent(
            root=self.root,
            session_id=self.session_id,
            identity=identity,
            path=self._snapshots[identity].path,
            state=WatchState.DISMISSED,
        )

    def retry(self, identity: ArrivalIdentity) -> WatchEvent:
        """Retry an arrival in FAILED state by returning it to DETECTED.

        Parameters
        ----------
        identity : ArrivalIdentity
            Failed arrival identity.

        Returns
        -------
        WatchEvent
            Event in DETECTED state.

        Raises
        ------
        ValueError
            If identity is not currently in FAILED state.
        """
        current = self._validate_current_identity(identity)
        if current != WatchState.FAILED:
            raise ValueError(
                f"Cannot retry identity in state {current}; retry is only valid for FAILED: {identity}"
            )
        self._states[identity] = WatchState.DETECTED
        return WatchEvent(
            root=self.root,
            session_id=self.session_id,
            identity=identity,
            path=self._snapshots[identity].path,
            state=WatchState.DETECTED,
        )
