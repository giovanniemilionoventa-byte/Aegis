import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type ConnectorCall, type EvidenceReport, type ExecutionRow } from "../api";

/**
 * Execution evidence, verified on demand.
 *
 * Until Phase 17 the chain was only ever checked as a side effect of the next
 * authorization on the same execution: there was no endpoint, no command and
 * no UI, so nobody outside the code could ask whether an execution's record
 * was intact. This is that question, asked from the operator's side.
 *
 * The verdict comes from the server recomputing every digest, not from the
 * stored hashes agreeing with themselves.
 */
export default function Evidence() {
  const [params] = useSearchParams();
  const [executions, setExecutions] = useState<ExecutionRow[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [report, setReport] = useState<EvidenceReport | null>(null);
  const [calls, setCalls] = useState<ConnectorCall[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api.executions().then(setExecutions).catch((err) =>
      setError(err instanceof Error ? err.message : "failed to load executions"),
    );
    // Deep link from Activity or an agent's verification run.
    const requested = params.get("execution");
    if (requested) open(requested);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const open = async (executionId: string) => {
    setError("");
    setSelected(executionId);
    setReport(null);
    setCalls([]);
    try {
      setReport(await api.evidence(executionId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to verify");
    }
    try {
      // Phase 19. Separate from the chain on purpose: the sealed events are
      // the authority, this is the observation of what the connector did.
      // A failure to load it must not make a verified chain look unverified.
      setCalls((await api.connectorCalls(executionId)).calls);
    } catch {
      setCalls([]);
    }
  };

  return (
    <>
      <h2 className="page-title">Execution evidence</h2>
      <p className="page-sub">
        Every decision is sealed into a hash chain. Open an execution to have the
        server recompute it and report whether the record has been altered.
      </p>
      {error && <p className="flash">{error}</p>}

      <div className="card">
        {executions.length === 0 && <div className="empty">No executions yet.</div>}
        {executions.length > 0 && (
          <table>
            <thead>
              <tr><th>Execution</th><th>Agent</th><th>Events</th><th>Started</th><th></th></tr>
            </thead>
            <tbody>
              {executions.map((row) => (
                <tr key={row.id}>
                  <td className="mono">{row.id.slice(0, 18)}</td>
                  <td className="mono">{row.agent_id.slice(0, 8)}</td>
                  <td className="mono">{row.event_count}</td>
                  <td className="mono">{new Date(row.created_at).toLocaleString()}</td>
                  <td>
                    <button className="btn small" onClick={() => open(row.id)}>
                      Verify
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {selected && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
            <h3 style={{ margin: 0 }} className="mono">{selected.slice(0, 24)}</h3>
            {report && (
              <span className={"badge badge-" + (report.verdict.valid ? "ALLOW" : "BLOCK")}>
                {report.verdict.valid ? "chain intact" : `TAMPERED — ${report.verdict.reason}`}
              </span>
            )}
          </div>

          {!report && <div className="empty">Verifying…</div>}

          {report && (
            <>
              <table>
                <thead>
                  <tr>
                    <th>#</th><th>Call</th><th>Decision</th><th>Digest</th><th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {report.chain.map((event) => (
                    <tr key={event.event_id}>
                      <td className="mono">{event.seq}</td>
                      <td className="mono">
                        {event.resource_kind}.{event.action}
                        <br />
                        {event.scope}
                      </td>
                      <td>
                        <span className={"badge badge-" + event.decision}>{event.decision}</span>
                      </td>
                      <td>
                        <span className={"badge badge-" + (event.digest_matches ? "ALLOW" : "BLOCK")}>
                          {event.digest_matches ? "verified" : "mismatch"}
                        </span>
                      </td>
                      <td>{event.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {report.approvals.length > 0 && (
                <>
                  <h4 style={{ marginBottom: 6 }}>Human approvals</h4>
                  <table>
                    <thead>
                      <tr>
                        <th>Action</th><th>Status</th><th>Reviewer</th>
                        <th>Contract</th><th>Used by</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.approvals.map((approval) => (
                        <tr key={approval.id}>
                          <td className="mono">
                            {approval.resource_kind}.{approval.action} {approval.scope}
                          </td>
                          <td>
                            <span className={"badge badge-" + approval.status}>
                              {approval.status}
                            </span>
                          </td>
                          <td className="mono">
                            {approval.reviewed_by ? approval.reviewed_by.slice(0, 8) : "—"}
                          </td>
                          <td className="mono">
                            {approval.contract_id ?? "—"}
                            {approval.contract_version ? ` v${approval.contract_version}` : ""}
                          </td>
                          <td className="mono">
                            {approval.consumed_event_id
                              ? approval.consumed_event_id.slice(0, 8)
                              : "unused"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </>
          )}
        </div>
      )}

      {selected && calls.length > 0 && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Protected-service calls</h3>
          <p className="page-sub" style={{ marginTop: 0 }}>
            What the agent said it was doing, what it actually asked for, and
            whether anything reached the protected service. The middle column is
            the one Aegis ruled on. The first is agent-supplied text and is not
            evidence of anything except what the agent claimed.
          </p>
          <table>
            <thead>
              <tr>
                <th>Agent&rsquo;s account (untrusted)</th>
                <th>Operation ruled on</th>
                <th>Decision</th>
                <th>Actually ran</th>
                <th>Touched</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((call) => (
                <tr key={call.id}>
                  <td>
                    {call.declared_intent_untrusted ? (
                      <span
                        title={
                          call.intent_matches_operation === false
                            ? "This description does not match the operation that was sent."
                            : undefined
                        }
                      >
                        {call.intent_matches_operation === false && "⚠ "}
                        {call.declared_intent_untrusted}
                      </span>
                    ) : (
                      <span className="muted">none given</span>
                    )}
                  </td>
                  <td className="mono">{call.canonical_operation}</td>
                  <td>
                    {call.decision}
                    {call.approval_granted && " (approved)"}
                  </td>
                  <td>
                    {call.executed ? (
                      <span className="mono">{call.connector_operation}</span>
                    ) : (
                      <span className="muted">nothing</span>
                    )}
                  </td>
                  <td className="mono">{call.resource_ref ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
