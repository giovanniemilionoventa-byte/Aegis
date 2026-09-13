"""Phase 16.B evidence-chain / trajectory diagnostic.

Read-only diagnostic that imports the REAL, unmodified application modules
(no reimplementation, no mocked verifier) to inspect one execution's stored
events directly from the shared SQLite database. Intended to be copied into
a running Aegis container that already has `app` importable and
AEGIS_DATABASE_URL pointing at the live /data/aegis.db (control-plane and
enforcement-gateway both qualify) and run with:

    docker cp benchmarks/chain_probe.py aegis-control-plane:/tmp/chain_probe.py
    docker exec aegis-control-plane python /tmp/chain_probe.py <execution_id>

Prints one JSON object to stdout:
  - ordered (seq, decision, id, evidence_hash prefix, previous_evidence_hash
    prefix) rows for the execution
  - duplicate_seq / seq_gaps found by direct inspection (independent of the
    verifier, so a verifier bug would not hide a real gap/duplicate)
  - the REAL assert_execution_evidence_integrity() verdict (PASS or the
    exact EvidenceIntegrityError reason)
  - the REAL reconstruct_trajectory_state() authorized-vs-total counts
    (confirms BLOCK events are excluded from authorized progress)
"""

from __future__ import annotations

import json
import sys

from app.database import SessionLocal
from app.services.evidence_verifier import (
    EvidenceIntegrityError,
    assert_execution_evidence_integrity,
)
from app.engines.trajectory import (
    owned_execution_events,
    reconstruct_trajectory_state,
)
from app import models


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: chain_probe.py <execution_id>"}))
        sys.exit(1)
    execution_id = sys.argv[1]

    db = SessionLocal()
    try:
        execution = (
            db.query(models.Execution)
            .filter(models.Execution.id == execution_id)
            .first()
        )
        if execution is None:
            print(json.dumps({"error": "execution not found", "execution_id": execution_id}))
            return

        events = owned_execution_events(db, execution_id)
        rows = []
        seqs = []
        for e in events:
            seqs.append(int(e.seq or 0))
            rows.append(
                {
                    "seq": e.seq,
                    "decision": e.decision,
                    "id": e.id,
                    "request_id": e.request_id,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                    "evidence_hash_prefix": (e.evidence_hash or "")[:12],
                    "previous_evidence_hash_prefix": (e.previous_evidence_hash or "")[:12],
                }
            )

        duplicates = sorted({s for s in seqs if seqs.count(s) > 1})
        gaps = []
        for a, b in zip(seqs, seqs[1:]):
            if b != a + 1:
                gaps.append({"after_seq": a, "next_seq": b})

        verifier_result = {"pass": True, "reason": None}
        try:
            assert_execution_evidence_integrity(db, execution_id)
        except EvidenceIntegrityError as exc:
            verifier_result = {"pass": False, "reason": exc.reason}

        state = reconstruct_trajectory_state(db, execution_id)
        trajectory_summary = None
        if state is not None:
            trajectory_summary = {
                "total_events": len(state.events),
                "authorized_count": len(state.authorized_actions),
                "decisions_in_order": [a.decision for a in state.events],
                "authorized_decisions": [a.decision for a in state.authorized_actions],
                "last_valid_progress": (
                    {
                        "seq": state.last_valid_progress.seq,
                        "resource_kind": state.last_valid_progress.resource_kind,
                        "action": state.last_valid_progress.action,
                    }
                    if state.last_valid_progress
                    else None
                ),
            }

        out = {
            "execution_id": execution_id,
            "event_count": len(events),
            "seq_sequence": seqs,
            "duplicate_seq": duplicates,
            "seq_gaps": gaps,
            "evidence_chain_tip_prefix": (execution.evidence_chain_tip or "")[:12],
            "verifier_result": verifier_result,
            "trajectory_summary": trajectory_summary,
            "rows": rows,
        }
        print(json.dumps(out, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
