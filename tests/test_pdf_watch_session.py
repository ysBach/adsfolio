from dataclasses import replace
from enum import Enum
from pathlib import Path

import pytest

from adsfolio.pdf.watch_session import (
    ArrivalIdentity,
    FileSnapshot,
    WatchEvent,
    WatchOutcome,
    WatchSession,
    WatchSource,
    WatchState,
)


def snapshot(
    path: Path | str,
    *,
    size_bytes: int = 100,
    mtime_ns: int = 1_000_000,
    device: int = 1,
    inode: int = 1,
    birth_time_ns: int | None = None,
) -> FileSnapshot:
    return FileSnapshot(
        path=Path(path),
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        device=device,
        inode=inode,
        birth_time_ns=birth_time_ns,
    )


def test_watch_state_and_source_are_str_enum_subclasses():
    assert issubclass(WatchState, (str, Enum))
    assert issubclass(WatchSource, (str, Enum))
    assert isinstance(WatchState.DETECTED, str)
    assert isinstance(WatchSource.CREATED, str)
    assert WatchState.DETECTED.value == "detected"
    assert WatchSource.CREATED.value == "created"


def test_preexisting_identity_is_rearrival_only_when_content_facts_change(tmp_path):
    before = snapshot(tmp_path / "existing.pdf", device=1, inode=2, mtime_ns=3)
    session = WatchSession(Path(tmp_path), "session-1", [before])
    assert session.observe(replace(before), WatchSource.MODIFIED) is None
    changed = replace(before, size_bytes=20, mtime_ns=4)
    event = session.observe(changed, WatchSource.MODIFIED)
    assert event is not None
    assert event.identity.generation == 1
    assert event.identity not in session.baseline
    assert session.observe(changed, WatchSource.RECONCILE) is None


def test_created_event_during_baseline_is_new_even_when_scan_sees_it(tmp_path):
    session = WatchSession.starting(Path(tmp_path), "session-1")
    created = snapshot(tmp_path / "new.pdf", device=1, inode=3)
    event = session.observe(created, WatchSource.CREATED)
    session.finish_baseline([created])
    assert event is not None
    assert event.identity not in session.baseline


def test_creation_generation_distinguishes_recycled_inode(tmp_path):
    existing = snapshot(tmp_path / "old.pdf", device=1, inode=2)
    session = WatchSession(Path(tmp_path), "session-1", [existing])
    recreated = snapshot(tmp_path / "new.pdf", device=1, inode=2)
    event = session.observe(recreated, WatchSource.CREATED)
    assert event is not None
    assert event.identity.generation == 1
    assert event.identity not in session.baseline


def test_moved_in_creates_new_generation(tmp_path):
    existing = snapshot(tmp_path / "old.pdf", device=1, inode=5)
    session = WatchSession(Path(tmp_path), "session-1", [existing])
    moved = snapshot(tmp_path / "moved_in.pdf", device=1, inode=5)
    event = session.observe(moved, WatchSource.MOVED_IN)
    assert event is not None
    assert event.identity.generation == 1


def test_multiple_recycled_inode_generations(tmp_path):
    existing = snapshot(tmp_path / "old.pdf", device=1, inode=7)
    session = WatchSession(Path(tmp_path), "session-1", [existing])

    # First recreation after baseline -> gen 1
    rec1 = snapshot(tmp_path / "new1.pdf", device=1, inode=7)
    ev1 = session.observe(rec1, WatchSource.CREATED)
    assert ev1 is not None
    assert ev1.identity.generation == 1

    # Inode disappears on disk (e.g. file deleted)
    session.reconcile([])

    # Second recreation after disappearance -> gen 2
    rec2 = snapshot(tmp_path / "new2.pdf", device=1, inode=7)
    ev2 = session.observe(rec2, WatchSource.CREATED)
    assert ev2 is not None
    assert ev2.identity.generation == 2


def test_deterministic_terminal_and_retry_transitions(tmp_path):
    created = snapshot(
        tmp_path / "sample.pdf", device=1, inode=10, size_bytes=100, mtime_ns=1000
    )
    session = WatchSession(Path(tmp_path), "session-1")
    event = session.observe(created, WatchSource.CREATED)
    assert event is not None
    identity = event.identity

    # Dismiss terminal state
    assert session.dismiss(identity).state is WatchState.DISMISSED

    # Unchanged reconcile does nothing for dismissed
    unchanged = replace(created)
    assert session.observe(unchanged, WatchSource.RECONCILE) is None

    # New arrival for testing FAILED / RETRY / DEFERRED / MODIFIED
    created2 = snapshot(
        tmp_path / "sample2.pdf", device=1, inode=11, size_bytes=200, mtime_ns=2000
    )
    event2 = session.observe(created2, WatchSource.CREATED)
    assert event2 is not None
    identity2 = event2.identity

    # Complete as FAILED
    assert session.complete(identity2, WatchState.FAILED).state is WatchState.FAILED

    # Unchanged facts do not auto-retry FAILED
    assert session.observe(replace(created2), WatchSource.MODIFIED) is None

    # Explicit retry returns to DETECTED
    assert session.retry(identity2).state is WatchState.DETECTED

    # Complete as DEFERRED
    assert session.complete(identity2, WatchState.DEFERRED).state is WatchState.DEFERRED

    # Unchanged modified/reconcile does not wake DEFERRED
    assert session.observe(created2, WatchSource.MODIFIED) is None
    assert session.observe(created2, WatchSource.RECONCILE) is None

    # Changed facts return DEFERRED to DETECTED
    changed2 = replace(created2, size_bytes=250, mtime_ns=2500)
    assert session.observe(changed2, WatchSource.MODIFIED).state is WatchState.DETECTED


def test_reconciliation_discovers_untracked_arrivals_and_ignores_baseline(tmp_path):
    existing = snapshot(tmp_path / "baseline.pdf", device=1, inode=20)
    session = WatchSession(Path(tmp_path), "session-1", [existing])

    # Baseline file seen during reconcile returns None
    assert session.observe(existing, WatchSource.RECONCILE) is None

    # Unknown file seen during reconcile is detected as a new arrival
    untracked = snapshot(tmp_path / "untracked.pdf", device=1, inode=21)
    event = session.observe(untracked, WatchSource.RECONCILE)
    assert event is not None
    assert event.state is WatchState.DETECTED
    assert event.identity not in session.baseline


def test_begin_processing_and_completion_states(tmp_path):
    created = snapshot(tmp_path / "paper.pdf", device=1, inode=30)
    session = WatchSession(Path(tmp_path), "session-1")
    event = session.observe(created, WatchSource.CREATED)
    assert event is not None
    identity = event.identity

    # Begin processing
    proc_event = session.begin_processing(identity)
    assert proc_event.state is WatchState.PROCESSING

    # Complete to RENAMED
    resolved_event = session.complete(identity, WatchState.RENAMED)
    assert resolved_event.state is WatchState.RENAMED

    # Terminal state ignores a modification event whose facts are unchanged
    assert session.observe(replace(created), WatchSource.MODIFIED) is None


def test_changed_terminal_or_failed_file_becomes_new_generation(tmp_path):
    for state in (WatchState.RENAMED, WatchState.FAILED, WatchState.DISMISSED):
        session = WatchSession(Path(tmp_path), "session-1")
        created = snapshot(tmp_path / "paper.pdf", inode=30)
        first = session.observe(created, WatchSource.CREATED)
        assert first is not None
        if state is WatchState.DISMISSED:
            session.dismiss(first.identity)
        else:
            session.begin_processing(first.identity)
            session.complete(first.identity, state)
        changed = replace(created, size_bytes=500, mtime_ns=5000)
        second = session.observe(changed, WatchSource.MODIFIED)
        assert second is not None
        assert second.identity.generation == 1
        assert session.state(first.identity) is WatchState.STALE
        assert session.state(second.identity) is WatchState.DETECTED


def test_invalid_transitions_raise_value_error(tmp_path):
    created = snapshot(tmp_path / "paper.pdf", device=1, inode=40)
    session = WatchSession(Path(tmp_path), "session-1")
    event = session.observe(created, WatchSource.CREATED)
    assert event is not None
    identity = event.identity

    stale_identity = ArrivalIdentity(
        device=1, inode=999, birth_time_ns=None, generation=0
    )

    # Unknown/stale identity raises ValueError
    with pytest.raises(ValueError, match="Unknown or stale identity"):
        session.begin_processing(stale_identity)

    with pytest.raises(ValueError, match="Unknown or stale identity"):
        session.complete(stale_identity, WatchState.RENAMED)

    with pytest.raises(ValueError, match="Unknown or stale identity"):
        session.dismiss(stale_identity)

    with pytest.raises(ValueError, match="Unknown or stale identity"):
        session.retry(stale_identity)

    # Invalid retry from DETECTED
    with pytest.raises(ValueError, match="Cannot retry"):
        session.retry(identity)

    # Invalid completion targets
    with pytest.raises(ValueError, match="Invalid completion state"):
        session.complete(identity, WatchState.DETECTED)

    with pytest.raises(ValueError, match="Invalid completion state"):
        session.complete(identity, WatchState.PROCESSING)

    # Double processing
    session.begin_processing(identity)
    with pytest.raises(ValueError, match="already being processed"):
        session.begin_processing(identity)


def test_events_carry_normalized_root_and_session_id(tmp_path):
    root_str = str(tmp_path)
    session = WatchSession(root_str, "session-xyz")
    created = snapshot(tmp_path / "file.pdf", device=1, inode=50)
    event = session.observe(created, WatchSource.CREATED)
    assert event is not None
    assert isinstance(event, WatchEvent)
    assert event.root == Path(tmp_path).resolve()
    assert event.session_id == "session-xyz"
    assert event.path == Path(tmp_path / "file.pdf")


def test_watch_outcome_dataclass(tmp_path):
    created = snapshot(tmp_path / "file.pdf", device=1, inode=60)
    session = WatchSession(Path(tmp_path), "session-1")
    event = session.observe(created, WatchSource.CREATED)
    assert event is not None

    outcome = WatchOutcome(
        event=event,
        destination=tmp_path / "renamed.pdf",
        bibcode="2025RNAAS...9..338Z",
        message="Success",
    )
    assert outcome.event == event
    assert outcome.destination == tmp_path / "renamed.pdf"
    assert outcome.bibcode == "2025RNAAS...9..338Z"
    assert outcome.message == "Success"


def test_file_snapshot_from_path(tmp_path):
    test_file = tmp_path / "doc.pdf"
    test_file.write_bytes(b"%PDF-1.4 test")

    snap = FileSnapshot.from_path(test_file)
    assert snap.path == test_file
    assert snap.size_bytes == len(b"%PDF-1.4 test")
    assert snap.device > 0
    assert snap.inode > 0
    assert snap.mtime_ns > 0


def test_session_query_accessors_and_baseline_check(tmp_path):
    baseline_file = snapshot(tmp_path / "base.pdf", device=1, inode=100)
    session = WatchSession(Path(tmp_path), "session-1", [baseline_file])

    assert session.is_baseline(baseline_file)
    assert session.is_baseline(
        ArrivalIdentity(device=1, inode=100, birth_time_ns=None, generation=0)
    )

    new_file = snapshot(tmp_path / "new.pdf", device=1, inode=101)
    event = session.observe(new_file, WatchSource.CREATED)
    assert event is not None
    assert not session.is_baseline(new_file)
    assert not session.is_baseline(event.identity)

    assert session.snapshot(event.identity) == new_file
    assert session.state(event.identity) is WatchState.DETECTED

    unknown_id = ArrivalIdentity(device=1, inode=999, birth_time_ns=None, generation=0)
    assert session.snapshot(unknown_id) is None
    assert session.state(unknown_id) is None


def test_dismiss_from_deferred_and_failed(tmp_path):
    file1 = snapshot(tmp_path / "f1.pdf", device=1, inode=110)
    session = WatchSession(Path(tmp_path), "session-1")
    ev1 = session.observe(file1, WatchSource.CREATED)
    assert ev1 is not None
    session.complete(ev1.identity, WatchState.DEFERRED)
    assert session.dismiss(ev1.identity).state is WatchState.DISMISSED

    file2 = snapshot(tmp_path / "f2.pdf", device=1, inode=111)
    ev2 = session.observe(file2, WatchSource.CREATED)
    assert ev2 is not None
    session.complete(ev2.identity, WatchState.FAILED)
    assert session.dismiss(ev2.identity).state is WatchState.DISMISSED


def test_all_terminal_completion_states(tmp_path):
    for terminal_state in (
        WatchState.RENAMED,
        WatchState.RESOLVED,
        WatchState.UNRESOLVED,
        WatchState.STALE,
        WatchState.DISMISSED,
    ):
        file = snapshot(
            tmp_path / f"{terminal_state.value}.pdf",
            device=1,
            inode=200 + hash(terminal_state.value) % 100,
        )
        session = WatchSession(Path(tmp_path), "session-1")
        ev = session.observe(file, WatchSource.CREATED)
        assert ev is not None
        assert session.complete(ev.identity, terminal_state).state is terminal_state
        assert session.state(ev.identity) is terminal_state
        with pytest.raises(
            ValueError, match="Cannot complete identity from terminal state"
        ):
            session.complete(ev.identity, WatchState.RENAMED)


def test_superseded_identity_marked_stale_and_rejects_transitions(tmp_path):
    snap1 = snapshot(tmp_path / "file.pdf", device=1, inode=300)
    session = WatchSession(Path(tmp_path), "session-1")
    ev1 = session.observe(snap1, WatchSource.CREATED)
    assert ev1 is not None
    old_id = ev1.identity
    assert old_id.generation == 0

    # File disappears during reconciliation
    session.reconcile([])
    assert session.state(old_id) is WatchState.STALE

    # Recreated with new generation
    snap2 = snapshot(tmp_path / "file.pdf", device=1, inode=300)
    events = session.reconcile([snap2])
    assert len(events) == 1
    new_id = events[0].identity
    assert new_id.generation == 1
    assert session.state(old_id) is WatchState.STALE

    # Old superseded identity rejects all transitions
    with pytest.raises(ValueError, match="stale|non-current|Unknown"):
        session.begin_processing(old_id)
    with pytest.raises(ValueError, match="stale|non-current|Unknown"):
        session.complete(old_id, WatchState.RENAMED)
    with pytest.raises(ValueError, match="stale|non-current|Unknown"):
        session.dismiss(old_id)
    with pytest.raises(ValueError, match="stale|non-current|Unknown"):
        session.retry(old_id)


def test_duplicate_created_events_coalesce_without_advancing_generation(tmp_path):
    snap = snapshot(tmp_path / "same.pdf", device=1, inode=310, size_bytes=100)
    session = WatchSession(Path(tmp_path), "session-1")
    ev1 = session.observe(snap, WatchSource.CREATED)
    assert ev1 is not None
    assert ev1.identity.generation == 0

    # Duplicate CREATED event for active identity does not re-emit or bump generation
    ev2 = session.observe(snap, WatchSource.CREATED)
    assert ev2 is None

    # Duplicate MOVED_IN event for active identity does not re-emit or bump generation
    ev3 = session.observe(snap, WatchSource.MOVED_IN)
    assert ev3 is None


def test_reconciliation_retires_disappeared_baseline_identity_and_admits_new_generation(
    tmp_path,
):
    base_snap = snapshot(tmp_path / "base.pdf", device=1, inode=320)
    session = WatchSession(Path(tmp_path), "session-1", [base_snap])
    assert session.is_baseline(base_snap)

    # Baseline file disappeared
    session.reconcile([])
    assert not session.is_baseline(base_snap)

    # Inode reused for a new file
    recreated_snap = snapshot(tmp_path / "recreated.pdf", device=1, inode=320)
    events = session.reconcile([recreated_snap])
    assert len(events) == 1
    assert events[0].identity.generation == 1
    assert events[0].state is WatchState.DETECTED
    assert events[0].identity not in session.baseline


def test_dismiss_rejected_while_processing(tmp_path):
    snap = snapshot(tmp_path / "proc.pdf", device=1, inode=330)
    session = WatchSession(Path(tmp_path), "session-1")
    ev = session.observe(snap, WatchSource.CREATED)
    assert ev is not None

    session.begin_processing(ev.identity)
    assert session.state(ev.identity) is WatchState.PROCESSING

    with pytest.raises(ValueError, match="processing|Cannot dismiss"):
        session.dismiss(ev.identity)

    assert session.state(ev.identity) is WatchState.PROCESSING
    assert (
        session.complete(ev.identity, WatchState.RESOLVED).state is WatchState.RESOLVED
    )


def test_duplicate_created_coalesces_across_all_terminal_and_active_states(tmp_path):
    terminal_states = [
        WatchState.DISMISSED,
        WatchState.RENAMED,
        WatchState.RESOLVED,
        WatchState.UNRESOLVED,
        WatchState.FAILED,
    ]
    for i, state in enumerate(terminal_states):
        snap = snapshot(tmp_path / f"file_{state.value}.pdf", device=1, inode=400 + i)
        session = WatchSession(Path(tmp_path), "session-1")
        ev = session.observe(snap, WatchSource.CREATED)
        assert ev is not None
        initial_identity = ev.identity
        assert initial_identity.generation == 0

        if state is WatchState.DISMISSED:
            session.dismiss(initial_identity)
        else:
            session.complete(initial_identity, state)
        assert session.state(initial_identity) is state

        # Duplicate CREATED must coalesce, return None, and not advance generation
        dup_created = session.observe(snap, WatchSource.CREATED)
        assert dup_created is None
        assert session.state(initial_identity) is state
        assert session.snapshot(initial_identity) == snap

        # Duplicate MOVED_IN must also coalesce, return None, and not advance generation
        dup_moved = session.observe(snap, WatchSource.MOVED_IN)
        assert dup_moved is None
        assert session.state(initial_identity) is state


def test_reconciliation_does_not_stale_identity_during_processing(tmp_path):
    snap = snapshot(tmp_path / "proc_move.pdf", device=1, inode=450)
    session = WatchSession(Path(tmp_path), "session-1")
    ev = session.observe(snap, WatchSource.CREATED)
    assert ev is not None

    session.begin_processing(ev.identity)
    assert session.state(ev.identity) is WatchState.PROCESSING

    # Empty scan while processing is running (source file moved/renamed)
    events = session.reconcile([])
    assert events == []
    # PROCESSING identity must retain ownership and NOT be marked STALE
    assert session.state(ev.identity) is WatchState.PROCESSING

    # Complete successfully
    completed_ev = session.complete(ev.identity, WatchState.RENAMED)
    assert completed_ev.state is WatchState.RENAMED
    assert session.state(ev.identity) is WatchState.RENAMED


def test_arrival_during_reconciliation_scan_survives_older_snapshot(tmp_path):
    session = WatchSession(Path(tmp_path), "session-1")

    # Start scan token
    token = session.start_scan()

    # Arrival observed while scan was in flight
    new_snap = snapshot(tmp_path / "mid_scan.pdf", device=1, inode=460)
    ev = session.observe(new_snap, WatchSource.CREATED)
    assert ev is not None
    assert ev.state is WatchState.DETECTED

    # Scan returns results without mid_scan.pdf (because scan began before file arrived)
    reconcile_events = session.reconcile([], scan_token=token)
    assert reconcile_events == []

    # Mid-scan arrival must not be retired or marked STALE
    assert session.state(ev.identity) is WatchState.DETECTED
    assert session.snapshot(ev.identity) == new_snap
    assert session.begin_processing(ev.identity).state is WatchState.PROCESSING


def test_terminal_identity_moved_in_at_different_path_coalesces_without_new_generation(
    tmp_path,
):
    snap1 = snapshot(tmp_path / "original.pdf", device=1, inode=520)
    session = WatchSession(Path(tmp_path), "session-1")
    ev1 = session.observe(snap1, WatchSource.CREATED)
    assert ev1 is not None
    session.complete(ev1.identity, WatchState.RESOLVED)

    # File moved/renamed to a different path
    snap2 = snapshot(tmp_path / "renamed_dest.pdf", device=1, inode=520)
    dup = session.observe(snap2, WatchSource.MOVED_IN)

    assert dup is None
    assert session.state(ev1.identity) is WatchState.RESOLVED
    assert session.snapshot(ev1.identity).path == tmp_path / "renamed_dest.pdf"
    assert ev1.identity.generation == 0


def test_missing_while_processing_complete_allows_replacement_new_generation(tmp_path):
    snap1 = snapshot(tmp_path / "proc1.pdf", device=1, inode=500)
    session = WatchSession(Path(tmp_path), "session-1")
    ev1 = session.observe(snap1, WatchSource.CREATED)
    assert ev1 is not None
    session.begin_processing(ev1.identity)

    # File disappears while processing
    session.reconcile([])

    # Complete returns completion event and retires key
    completed_ev = session.complete(ev1.identity, WatchState.UNRESOLVED)
    assert completed_ev.state is WatchState.UNRESOLVED

    # Later replacement with same inode is admitted as new generation
    snap2 = snapshot(tmp_path / "proc2.pdf", device=1, inode=500)
    ev2 = session.observe(snap2, WatchSource.CREATED)
    assert ev2 is not None
    assert ev2.identity.generation == 1
    assert ev2.state is WatchState.DETECTED


def test_missing_while_processing_observed_at_destination_stays_coalesced(tmp_path):
    snap_src = snapshot(tmp_path / "source.pdf", device=1, inode=510)
    session = WatchSession(Path(tmp_path), "session-1")
    ev = session.observe(snap_src, WatchSource.CREATED)
    assert ev is not None
    session.begin_processing(ev.identity)

    # File temporarily missing from source during move
    session.reconcile([])

    # Observed at destination
    snap_dest = snapshot(tmp_path / "dest.pdf", device=1, inode=510)
    assert session.observe(snap_dest, WatchSource.MOVED_IN) is None
    assert session.complete(ev.identity, WatchState.RENAMED).state is WatchState.RENAMED
    assert session.observe(snap_dest, WatchSource.MOVED_IN) is None


@pytest.mark.parametrize("source", [WatchSource.CREATED, WatchSource.MOVED_IN])
def test_changed_deferred_facts_wake_on_native_arrival_observation(tmp_path, source):
    initial = snapshot(tmp_path / "deferred.pdf", device=1, inode=530)
    changed = replace(initial, size_bytes=200, mtime_ns=2_000_000)
    session = WatchSession(Path(tmp_path), "session-1")
    event = session.observe(initial, WatchSource.CREATED)
    assert event is not None
    session.complete(event.identity, WatchState.DEFERRED)

    wake_event = session.observe(changed, source)

    assert wake_event is not None
    assert wake_event.identity == event.identity
    assert wake_event.state is WatchState.DETECTED
    assert session.state(event.identity) is WatchState.DETECTED
    assert session.snapshot(event.identity) == changed


def test_changed_deferred_facts_wake_during_full_reconciliation(tmp_path):
    initial = snapshot(tmp_path / "deferred.pdf", device=1, inode=540)
    changed = replace(initial, size_bytes=200, mtime_ns=2_000_000)
    session = WatchSession(Path(tmp_path), "session-1")
    event = session.observe(initial, WatchSource.CREATED)
    assert event is not None
    session.complete(event.identity, WatchState.DEFERRED)

    events = session.reconcile([changed])

    assert len(events) == 1
    assert events[0].identity == event.identity
    assert events[0].state is WatchState.DETECTED
    assert session.state(event.identity) is WatchState.DETECTED
    assert session.snapshot(event.identity) == changed


def test_processing_arrival_after_scan_boundary_survives_older_absent_scan(tmp_path):
    session = WatchSession(Path(tmp_path), "session-1")
    scan_token = session.start_scan()
    arrived = snapshot(tmp_path / "after-boundary.pdf", device=1, inode=550)
    event = session.observe(arrived, WatchSource.CREATED)
    assert event is not None
    session.begin_processing(event.identity)

    assert session.reconcile([], scan_token=scan_token) == []
    completed = session.complete(event.identity, WatchState.RESOLVED)
    duplicate = session.observe(arrived, WatchSource.CREATED)

    assert completed.state is WatchState.RESOLVED
    assert session.state(event.identity) is WatchState.RESOLVED
    assert duplicate is None
    assert session.snapshot(event.identity) == arrived


def _arrive(session, snap, source=WatchSource.CREATED):
    event = session.observe(snap, source)
    assert event is not None
    return event


def test_replacement_at_same_path_with_new_inode_is_new_arrival(tmp_path):
    path = tmp_path / "paper.pdf"
    old = snapshot(path, inode=10)
    session = WatchSession(Path(tmp_path), "s", [old])
    new = snapshot(path, inode=11, size_bytes=300, mtime_ns=9)
    # Atomic replace reaches the session as a rename inside the root.
    event = _arrive(session, new, WatchSource.MODIFIED)
    assert event.identity.inode == 11
    session.reconcile([new])
    assert not session.is_baseline(old)


def test_same_inode_changed_bytes_is_new_generation_then_quiet(tmp_path):
    path = tmp_path / "paper.pdf"
    session = WatchSession(Path(tmp_path), "s")
    first = _arrive(session, snapshot(path, inode=12))
    session.begin_processing(first.identity)
    session.complete(first.identity, WatchState.RESOLVED)
    changed = snapshot(path, inode=12, size_bytes=101, mtime_ns=2_000_000)
    second = _arrive(session, changed, WatchSource.MODIFIED)
    assert second.identity.generation == first.identity.generation + 1
    assert session.observe(changed, WatchSource.RECONCILE) is None
    assert session.reconcile([changed]) == []


def test_delete_then_new_name_is_new_arrival(tmp_path):
    old = snapshot(tmp_path / "old.pdf", inode=20)
    session = WatchSession(Path(tmp_path), "s")
    first = _arrive(session, old)
    session.begin_processing(first.identity)
    session.complete(first.identity, WatchState.RENAMED)
    assert session.reconcile([]) == []
    new = snapshot(tmp_path / "new.pdf", inode=21)
    events = session.reconcile([new])
    assert [e.path.name for e in events] == ["new.pdf"]


def test_partial_copy_growth_does_not_emit_until_deferred_wakes(tmp_path):
    path = tmp_path / "copy.pdf"
    session = WatchSession(Path(tmp_path), "s")
    first = _arrive(session, snapshot(path, inode=30, size_bytes=10))
    grown = snapshot(path, inode=30, size_bytes=50, mtime_ns=5)
    assert session.observe(grown, WatchSource.MODIFIED) is None
    assert session.snapshot(first.identity) == grown
    session.begin_processing(first.identity)
    session.complete(first.identity, WatchState.DEFERRED)
    done = snapshot(path, inode=30, size_bytes=90, mtime_ns=6)
    woken = session.observe(done, WatchSource.MODIFIED)
    assert woken is not None and woken.identity == first.identity
    assert session.observe(done, WatchSource.RECONCILE) is None


def test_change_during_processing_becomes_new_generation_on_complete(tmp_path):
    path = tmp_path / "busy.pdf"
    session = WatchSession(Path(tmp_path), "s")
    first = _arrive(session, snapshot(path, inode=40))
    session.begin_processing(first.identity)
    changed = snapshot(path, inode=40, size_bytes=222, mtime_ns=7)
    assert session.observe(changed, WatchSource.MODIFIED) is None
    assert session.drain_followups() == []
    session.complete(first.identity, WatchState.RENAMED)
    (follow,) = session.drain_followups()
    assert follow.identity.generation == first.identity.generation + 1
    assert session.state(follow.identity) is WatchState.DETECTED
    assert session.snapshot(follow.identity) == changed
    assert session.drain_followups() == []


def test_unchanged_observation_during_processing_has_no_followup(tmp_path):
    path = tmp_path / "busy.pdf"
    session = WatchSession(Path(tmp_path), "s")
    first = _arrive(session, snapshot(path, inode=41))
    session.begin_processing(first.identity)
    assert session.observe(snapshot(path, inode=41), WatchSource.MODIFIED) is None
    session.complete(first.identity, WatchState.RESOLVED)
    assert session.drain_followups() == []


def test_change_then_disappearance_during_processing_has_no_followup(tmp_path):
    path = tmp_path / "gone.pdf"
    session = WatchSession(Path(tmp_path), "s")
    first = _arrive(session, snapshot(path, inode=42))
    session.begin_processing(first.identity)
    session.observe(snapshot(path, inode=42, size_bytes=5), WatchSource.MODIFIED)
    session.reconcile([])
    session.complete(first.identity, WatchState.RENAMED)
    assert session.drain_followups() == []


def test_self_rename_with_unchanged_facts_never_loops(tmp_path):
    session = WatchSession(Path(tmp_path), "s")
    first = _arrive(session, snapshot(tmp_path / "raw.pdf", inode=50))
    session.begin_processing(first.identity)
    session.complete(first.identity, WatchState.RENAMED)
    renamed = snapshot(tmp_path / "Author+2020.pdf", inode=50)
    for source in (WatchSource.MODIFIED, WatchSource.MOVED_IN, WatchSource.CREATED):
        assert session.observe(renamed, source) is None
    assert session.reconcile([renamed]) == []
    assert session.snapshot(first.identity).path == renamed.path


def test_baseline_file_renamed_with_unchanged_facts_is_quiet(tmp_path):
    base = snapshot(tmp_path / "a.pdf", inode=60)
    session = WatchSession(Path(tmp_path), "s", [base])
    renamed = replace(base, path=tmp_path / "b.pdf")
    assert session.observe(renamed, WatchSource.MODIFIED) is None
    assert session.reconcile([renamed]) == []


def test_baseline_file_changed_during_startup_scan_is_found_by_reconcile(tmp_path):
    scanned = snapshot(tmp_path / "a.pdf", inode=61)
    session = WatchSession.starting(Path(tmp_path), "s")
    session.finish_baseline([scanned])
    now = replace(scanned, size_bytes=999, mtime_ns=77)
    (event,) = session.reconcile([now])
    assert event.identity.generation == 1
