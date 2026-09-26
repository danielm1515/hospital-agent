"""Staff-fixes design Task 5: `hospital_agent.repository.queue_page` - one statement with a
LATERAL join to each case's latest entry into AwaitingHumanReview, keyset-paginated.
"""
from datetime import UTC, datetime, timedelta

from hospital_agent import repository
from hospital_agent.naming import EscalationKind, State
from hospital_agent.repository import AuditEntry
from tests.driver import Driver


def escalate(sm, app_engine, patient_id: str = "P-10041") -> Driver:
    d = Driver(sm, app_engine, patient_id=patient_id)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    d.retry_exhausted()
    assert d.state is State.AWAITING_HUMAN_REVIEW
    return d


def _insert_entry_at(app_engine, case_id: str, patient_id: str, at: datetime) -> None:
    """A fresh Transition row into AwaitingHumanReview, at an exact chosen time - `audit_log`
    is append-only (`hospital_app` has INSERT/SELECT only, no UPDATE), so a deterministic tie
    between two cases (a real clock almost never produces one) is built by inserting a new
    latest row, not by editing an existing one."""
    with app_engine.begin() as conn:
        repository.insert_audit(conn, AuditEntry(
            case_id=case_id,
            patient_id=patient_id,
            record_type="Transition",
            event="HUMAN_APPROVED",
            state_before="Received",
            state_after="AwaitingHumanReview",
            rule_version="test",
            recorded_at=at,
        ))


def test_tie_break_is_case_id_ascending(sm, app_engine):
    a = escalate(sm, app_engine, "P-50001")
    b = escalate(sm, app_engine, "P-50002")
    same_time = datetime.now(UTC).replace(microsecond=0)
    _insert_entry_at(app_engine, a.case_id, a.patient_id, same_time)
    _insert_entry_at(app_engine, b.case_id, b.patient_id, same_time)
    lower_id, higher_id = sorted([a.case_id, b.case_id])

    with app_engine.connect() as conn:
        rows, has_more = repository.queue_page(conn, 50, None)

    ordered = [r.case_id for r in rows if r.case_id in (a.case_id, b.case_id)]
    assert ordered == [lower_id, higher_id]
    assert has_more is False


def test_tie_boundary_pagination_is_case_id_ascending_across_pages(sm, app_engine):
    """Fix round 1 (I2): 4 cases forced to the exact same entered_at, walked one row per
    page (limit=1) - the tie-break has to hold across the page boundary too, not just
    within one query's result set, or the walk would duplicate or skip a row."""
    made = [escalate(sm, app_engine, f"P-5100{i}") for i in range(4)]
    same_time = datetime.now(UTC).replace(microsecond=0)
    for d in made:
        _insert_entry_at(app_engine, d.case_id, d.patient_id, same_time)
    expected = sorted(d.case_id for d in made)

    seen: list[str] = []
    cursor = None
    for _ in range(len(made) + 1):
        with app_engine.connect() as conn:
            rows, has_more = repository.queue_page(conn, 1, cursor)
        seen.extend(row.case_id for row in rows if row.case_id in {d.case_id for d in made})
        if not has_more:
            break
        cursor = (rows[-1].entered_at, rows[-1].case_id)

    assert seen == expected  # exact order, no duplicates, none missing


def test_a_blocked_row_with_state_after_ahr_is_ignored(sm, app_engine):
    """Live data has a Blocked self-loop row with `state_after=AwaitingHumanReview` (a
    rejected HUMAN_APPROVED) - the FSM has no Transition from AwaitingHumanReview back into
    itself, so `record_type='Transition'` is what keeps this row from being read as a
    fresher entry than the real one."""
    d = escalate(sm, app_engine)
    later = datetime.now(UTC) + timedelta(hours=1)
    with app_engine.begin() as conn:
        repository.insert_audit(conn, AuditEntry(
            case_id=d.case_id,
            patient_id=d.patient_id,
            record_type="Blocked",
            event="HUMAN_APPROVED",
            state_before="AwaitingHumanReview",
            state_after="AwaitingHumanReview",
            rule_version="test",
            recorded_at=later,
        ))

    with app_engine.connect() as conn:
        rows, _ = repository.queue_page(conn, 50, None)
    [row] = [r for r in rows if r.case_id == d.case_id]
    assert row.entered_at < later


def test_pagination_across_pages_has_no_duplicates_and_nothing_missing(sm, app_engine):
    cases = [escalate(sm, app_engine, f"P-6000{i}") for i in range(5)]
    case_ids = {c.case_id for c in cases}

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        with app_engine.connect() as conn:
            rows, has_more = repository.queue_page(conn, 2, cursor)
        seen.extend(row.case_id for row in rows)
        pages += 1
        if not has_more:
            break
        cursor = (rows[-1].entered_at, rows[-1].case_id)
        assert pages < 10, "pagination did not terminate"

    assert pages == 3  # 2 + 2 + 1
    assert len(seen) == len(set(seen))  # no duplicates across pages
    assert set(seen) >= case_ids  # nothing missing


def test_a_case_with_no_transition_row_is_never_dropped_from_the_queue(sm, app_engine):
    """Fix round 1 (M4): the LATERAL join must be LEFT (outer), not inner - a case in
    AwaitingHumanReview always has its entry row in practice (only
    EscalationCoordinator.signal() writes that state, in the same transaction), but this
    forces the case in anyway (by moving `cases.state` directly, bypassing the FSM) to
    prove an inner join can never silently vanish a real case from the queue. `entered_at`
    falls back to `cases.updated_at`, `reasons` to `[]`."""
    from sqlalchemy import text

    d = Driver(sm, app_engine, patient_id="P-52000")
    d.submit()  # Received - no Transition row into AwaitingHumanReview exists for this case
    with app_engine.begin() as conn:
        conn.execute(text("UPDATE cases SET state = 'AwaitingHumanReview' WHERE case_id = :c"),
                    {"c": d.case_id})

    with app_engine.connect() as conn:
        rows, _ = repository.queue_page(conn, 50, None)
    [row] = [r for r in rows if r.case_id == d.case_id]

    assert row.entered_at == d.case.updated_at
    assert row.reasons == []
    assert row.returned_by is None


def test_a_fallback_case_is_reached_across_pages_at_its_coalesced_position(sm, app_engine):
    """Fix round 1 (N4): the COALESCE fallback (M4) is untested across pagination - a case
    with no Transition row must land at, and be reachable through, the page its
    *coalesced* entered_at (cases.updated_at) puts it at, not vanish because the keyset
    predicate compared the raw (null) entry.c.entered_at instead. Walked with limit=1."""
    from sqlalchemy import text

    newest = escalate(sm, app_engine, "P-53001")
    oldest = escalate(sm, app_engine, "P-53002")
    now = datetime.now(UTC).replace(microsecond=0)
    _insert_entry_at(app_engine, newest.case_id, newest.patient_id, now)
    _insert_entry_at(app_engine, oldest.case_id, oldest.patient_id, now - timedelta(hours=2))

    fallback = Driver(sm, app_engine, patient_id="P-53003")
    fallback.submit()  # no Transition row into AwaitingHumanReview at all
    between = now - timedelta(hours=1)  # strictly between newest and oldest
    with app_engine.begin() as conn:
        conn.execute(text("UPDATE cases SET state = 'AwaitingHumanReview', updated_at = :at WHERE case_id = :c"),
                    {"at": between, "c": fallback.case_id})

    expected = [newest.case_id, fallback.case_id, oldest.case_id]
    seen: list[str] = []
    cursor = None
    for _ in range(len(expected) + 1):
        with app_engine.connect() as conn:
            rows, has_more = repository.queue_page(conn, 1, cursor)
        seen.extend(row.case_id for row in rows if row.case_id in expected)
        if not has_more:
            break
        cursor = (rows[-1].entered_at, rows[-1].case_id)

    assert seen == expected  # the fallback case sits between the two real entries, always


def test_returned_by_and_reasons_come_from_the_latest_entry_row(sm, app_engine):
    """Pinned against the values `_escalation_reasons` / `_returned_by` used to compute by
    scanning the whole trace in Python (staff-fixes design Task 5's SQL replaces that scan,
    but must not change what a reviewer sees)."""
    from hospital_agent.naming import Component, Event

    d = escalate(sm, app_engine)
    approval_id = d.approval("request", patient_deadline=datetime.now(UTC) + timedelta(hours=24))
    d.sm.apply(d.case_id, Event.PATIENT_REPLY_REQUESTED,
              {"approval_id": approval_id, "reply_kind": "question", "requested_document": None,
               "content_hash": "HASH-MESSAGE"}, Component.EXTERNAL)
    d.sm.apply(d.case_id, Event.PATIENT_REPLY_SUBMITTED,
              {"reply_kind": "question", "content_hash": "HASH-REPLY"}, Component.SESSION_SERVICE)

    with app_engine.connect() as conn:
        rows, _ = repository.queue_page(conn, 50, None)
    [row] = [r for r in rows if r.case_id == d.case_id]
    assert row.returned_by == "patient_reply"
    assert row.escalation_kind is EscalationKind.RETRY_EXHAUSTED
    assert row.reasons == []  # RetryExhausted carries no policy_reasons
