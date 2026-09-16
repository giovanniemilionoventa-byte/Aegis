import { Fragment, useEffect, useState } from "react";
import { api, type Agent, type ApprovalRow } from "../api";

/**
 * Human approval.
 *
 * The previous version of this page showed a status, an action and two buttons.
 * That is not enough to approve anything responsibly: an operator could not see
 * which execution the request belonged to, which contract version it was made
 * under, what the parameters were, or when the request would expire. They were
 * approving blind.
 *
 * Approving here causes the agent's next attempt at that exact request to
 * execute, once. It is bound to the organization, agent, execution, request id,
 * action, scope, destination, payload digest and contract version — so a
 * modified request is not covered by it, and it cannot be used twice.
 */
export default function Approvals() {
  const [rows, setRows] = useState<ApprovalRow[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [error, setError] = useState("");

  const refresh = () =>
    Promise.all([api.approvals(), api.agents()])
      .then(([a, g]) => {
        setRows(a);
        setAgents(g);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "failed"));

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 4000);
    return () => window.clearInterval(timer);
  }, []);

  const decide = async (id: string, decision: string) => {
    setError("");
    try {
      await api.decide(id, decision);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "decision failed");
    }
  };

  const agentName = (id: string) =>
    agents.find((agent) => agent.id === id)?.name ?? id.slice(0, 8);

  const pending = rows.filter((row) => row.status === "pending");
  const decided = rows.filter((row) => row.status !== "pending");

  const expiryLabel = (row: ApprovalRow) => {
    if (!row.expires_at) return "—";
    const remaining = new Date(row.expires_at).getTime() - Date.now();
    if (remaining <= 0) return "expired";
    return `${Math.max(1, Math.round(remaining / 60000))} min left`;
  };

  const ageMinutes = (row: ApprovalRow) =>
    (Date.now() - new Date(row.created_at).getTime()) / 60000;

  const ageLabel = (row: ApprovalRow) => {
    const minutes = ageMinutes(row);
    if (minutes < 1) return "just now";
    return `${Math.round(minutes)} min ago`;
  };

  /**
   * An approval outlives the attempt that raised it.
   *
   * Agents wait a bounded time for a human and then give up, but the grant
   * stays valid until it expires. So a request from an abandoned run sits in
   * this queue looking exactly like one with an agent still retrying behind it,
   * and approving the wrong one means the operator believes they released the
   * action currently in flight when they did not.
   *
   * Aegis cannot tell the difference — it has no heartbeat, and re-submissions
   * of an already-pending request do not write new events. What it can do is
   * show the age and the execution, and say which question the operator is
   * actually being asked. That is a judgement aid, not a control: the grant is
   * bound and single-use either way.
   */
  const likelyAbandoned = (row: ApprovalRow) => ageMinutes(row) >= 3;

  return (
    <>
      <h2 className="page-title">Human approval</h2>
      <p className="page-sub">
        Approving lets the agent's next attempt at this exact request run, once.
        A changed request is not covered, and the grant cannot be reused.
      </p>
      <p className="page-sub">
        Check the execution before you decide. An agent that has given up waiting
        leaves its request here until it expires, so an old row can look exactly
        like a live one — and approving it releases nothing.
      </p>
      {error && <p className="flash">{error}</p>}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Waiting for you ({pending.length})</h3>
        {pending.length === 0 && <div className="empty">Nothing waiting.</div>}
        {pending.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Agent</th>
                <th>Action</th>
                <th>Execution</th>
                <th>Raised</th>
                <th>Expires</th>
                <th>Why</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {pending.map((row) => (
                <Fragment key={row.id}>
                  <tr>
                    <td className="mono">{agentName(row.agent_id)}</td>
                    <td className="mono">
                      {row.resource_kind}.{row.action} {row.scope}
                      {row.destination ? ` → ${row.destination}` : ""}
                    </td>
                    <td className="mono">
                      {row.execution_id ? row.execution_id.slice(0, 18) : "—"}
                    </td>
                    <td className="mono">
                      {ageLabel(row)}
                      {likelyAbandoned(row) && (
                        <>
                          {" "}
                          <span className="badge badge-medium" title="Agents wait a bounded time for a human and then stop. This request is old enough that the agent behind it has probably given up, so approving it may not release anything.">
                            agent may have stopped waiting
                          </span>
                        </>
                      )}
                    </td>
                    <td className="mono">{expiryLabel(row)}</td>
                    <td>{row.reason}</td>
                    <td>
                      <div className="row">
                        <button
                          className="btn secondary small"
                          onClick={() => setExpanded(expanded === row.id ? null : row.id)}
                        >
                          {expanded === row.id ? "Hide" : "Inspect"}
                        </button>
                        <button className="btn small" onClick={() => decide(row.id, "ALLOW")}>
                          Approve
                        </button>
                        <button
                          className="btn danger small"
                          onClick={() => decide(row.id, "BLOCK")}
                        >
                          Deny
                        </button>
                      </div>
                    </td>
                  </tr>
                  {expanded === row.id && (
                    <tr>
                      <td colSpan={7}>
                        <p className="page-sub" style={{ marginTop: 0 }}>
                          What this approval will authorize, exactly:
                        </p>
                        <table>
                          <tbody>
                            <tr>
                              <td>Execution</td>
                              <td className="mono">{row.execution_id ?? "—"}</td>
                            </tr>
                            <tr>
                              <td>Request</td>
                              <td className="mono">{row.request_id ?? "—"}</td>
                            </tr>
                            <tr>
                              <td>Contract</td>
                              <td className="mono">
                                {row.contract_id ?? "—"}
                                {row.contract_version ? ` v${row.contract_version}` : ""}
                              </td>
                            </tr>
                            <tr>
                              <td>Parameter digest</td>
                              <td className="mono">{row.param_hash ?? "—"}</td>
                            </tr>
                            <tr>
                              <td>Requested</td>
                              <td className="mono">
                                {new Date(row.created_at).toLocaleString()}
                              </td>
                            </tr>
                          </tbody>
                        </table>
                        <p className="page-sub">
                          The parameter digest is what binds this grant to the
                          exact payload. Aegis shows the digest rather than the
                          payload so approving cannot become a way to read data
                          the operator is not otherwise entitled to see.
                        </p>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>Decided</h3>
        {decided.length === 0 && <div className="empty">Nothing decided yet.</div>}
        {decided.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Agent</th>
                <th>Action</th>
                <th>Decision</th>
                <th>Used</th>
                <th>Reviewed</th>
              </tr>
            </thead>
            <tbody>
              {decided.map((row) => (
                <tr key={row.id}>
                  <td className="mono">{agentName(row.agent_id)}</td>
                  <td className="mono">
                    {row.resource_kind}.{row.action} {row.scope}
                  </td>
                  <td>
                    <span
                      className={
                        "badge badge-" + (row.status === "approved" ? "ALLOW" : "BLOCK")
                      }
                    >
                      {row.status}
                    </span>
                  </td>
                  <td>
                    {row.consumed_at ? (
                      <span className="badge badge-ALLOW">consumed once</span>
                    ) : row.status === "approved" ? (
                      <span className="badge badge-medium">not used yet</span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="mono">
                    {row.reviewed_at ? new Date(row.reviewed_at).toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
