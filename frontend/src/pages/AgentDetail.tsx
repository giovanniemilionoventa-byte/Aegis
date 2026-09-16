import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  api,
  type Agent,
  type Permission,
  type RuntimeContract,
  type VerificationRun,
} from "../api";

/**
 * One agent, showing real backend state and nothing else.
 *
 * Two honesty rules this page follows:
 *
 *   * Status is *configuration*, not liveness. Aegis records whether an agent
 *     is active or revoked; it has no heartbeat and cannot tell you whether the
 *     agent is running right now. The page says so rather than showing a green
 *     dot that means nothing.
 *
 *   * A run's status describes the request, not the security outcome. Whether
 *     each action was allowed or refused comes from the evidence chain the
 *     gateway wrote while authorizing it, which is linked, not restated.
 */
export default function AgentDetail() {
  const { agentId = "" } = useParams();
  const nav = useNavigate();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [contracts, setContracts] = useState<RuntimeContract[]>([]);
  const [runs, setRuns] = useState<VerificationRun[]>([]);
  const [rotated, setRotated] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const [a, p, c, r] = await Promise.all([
        api.agent(agentId),
        api.permissions(agentId).catch(() => [] as Permission[]),
        api.contracts(agentId).catch(() => [] as RuntimeContract[]),
        api.runs(agentId).catch(() => [] as VerificationRun[]),
      ]);
      setAgent(a);
      setPermissions(p);
      setContracts(c);
      setRuns(r);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load agent");
    }
  }, [agentId]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll only while a run is in flight, so the page is not busy for no reason.
  const inFlight = runs.some((run) => ["PENDING", "RUNNING"].includes(run.status));
  useEffect(() => {
    if (!inFlight) {
      if (timer.current) window.clearInterval(timer.current);
      timer.current = null;
      return;
    }
    timer.current = window.setInterval(load, 2000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [inFlight, load]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "action failed");
    } finally {
      setBusy(false);
    }
  };

  if (!agent) {
    return (
      <>
        <h2 className="page-title">Agent</h2>
        {error ? <p className="flash">{error}</p> : <div className="card"><div className="empty">Loading…</div></div>}
      </>
    );
  }

  const active = contracts.find((row) => row.status === "ACTIVE");
  const revoked = agent.status !== "active";
  const approvalRules = (active?.approval_rules ?? []) as Array<Record<string, any>>;
  const needsHuman = new Set(
    approvalRules.map((rule) => `${rule.resource_kind ?? "*"}.${rule.action ?? "*"}`),
  );

  return (
    <>
      <h2 className="page-title">{agent.name}</h2>
      <p className="page-sub">
        {agent.description || "No description."}
      </p>
      {error && <p className="flash">{error}</p>}

      {/* ---- authority summary ---- */}
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
          <h3 style={{ margin: 0 }}>Authority</h3>
          <div className="row">
            {revoked ? (
              <span className="badge badge-BLOCK">revoked</span>
            ) : active ? (
              <span className="badge badge-ALLOW">governed by a contract</span>
            ) : (
              <span className="badge badge-BLOCK">no active contract — denied</span>
            )}
          </div>
        </div>
        <p className="page-sub" style={{ marginBottom: 8 }}>
          Configuration state, not connectivity. Aegis has no heartbeat and does
          not know whether this agent is running.
        </p>
        <table>
          <tbody>
            <tr>
              <td>Agent id</td>
              <td className="mono">{agent.id}</td>
            </tr>
            <tr>
              <td>Organization</td>
              <td className="mono">{agent.organization_id}</td>
            </tr>
            <tr>
              <td>Created</td>
              <td className="mono">{new Date(agent.created_at).toLocaleString()}</td>
            </tr>
            {agent.revoked_at && (
              <tr>
                <td>Revoked</td>
                <td className="mono">{new Date(agent.revoked_at).toLocaleString()}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* ---- capabilities ---- */}
      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>Capabilities</h3>
        {permissions.length === 0 && (
          <div className="empty">
            No permissions. Every request from this agent is refused.
          </div>
        )}
        {permissions.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Capability</th>
                <th>Scope</th>
                <th>Effect</th>
                <th>In contract</th>
              </tr>
            </thead>
            <tbody>
              {permissions.map((perm) => {
                const inContract = (active?.capabilities ?? []).some((cap: any) => {
                  const kind = cap.resource_kind ?? cap.name;
                  const actions = (cap.actions ?? []) as string[];
                  return kind === perm.resource_kind && actions.includes(perm.action);
                });
                const human =
                  needsHuman.has(`${perm.resource_kind}.${perm.action}`) ||
                  needsHuman.has(`*.${perm.action}`);
                return (
                  <tr key={perm.id}>
                    <td className="mono">
                      {perm.resource_kind}.{perm.action}
                    </td>
                    <td className="mono">{perm.scope}</td>
                    <td>
                      {human ? (
                        <span className="badge badge-APPROVAL">needs a human</span>
                      ) : perm.effect === "allow" ? (
                        <span className="badge badge-ALLOW">allow</span>
                      ) : (
                        <span className="badge badge-BLOCK">deny</span>
                      )}
                    </td>
                    <td>
                      {inContract ? (
                        <span className="badge badge-ALLOW">yes</span>
                      ) : (
                        <span className="badge badge-BLOCK">
                          no — refused by the contract
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* ---- contract ---- */}
      <div className="card" style={{ marginTop: 16 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
          <h3 style={{ margin: 0 }}>Runtime contract</h3>
          {active && (
            <button
              className="btn danger small"
              disabled={busy}
              onClick={() =>
                act(() =>
                  api.setContractStatus(
                    agent.id,
                    active.contract_id,
                    active.version,
                    "REVOKED",
                  ),
                )
              }
            >
              Revoke contract
            </button>
          )}
        </div>
        {contracts.length === 0 && (
          <div className="empty">No contract. This agent has no authority.</div>
        )}
        {contracts.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Contract</th>
                <th>Ver</th>
                <th>Status</th>
                <th>Purpose</th>
                <th>Expires</th>
              </tr>
            </thead>
            <tbody>
              {contracts.map((row) => (
                <tr key={`${row.contract_id}-${row.version}`}>
                  <td className="mono">{row.contract_id}</td>
                  <td className="mono">{row.version}</td>
                  <td>
                    <span
                      className={"badge badge-" + (row.status === "ACTIVE" ? "ALLOW" : "BLOCK")}
                    >
                      {row.status}
                    </span>
                  </td>
                  <td>{row.purpose || "—"}</td>
                  <td className="mono">
                    {row.expires_at ? new Date(row.expires_at).toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* ---- runtime verification ---- */}
      <div className="card" style={{ marginTop: 16 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
          <h3 style={{ margin: 0 }}>Runtime verification</h3>
          <button
            className="btn small"
            disabled={busy || revoked || inFlight}
            onClick={() => act(() => api.requestRun(agent.id, "canonical"))}
          >
            {inFlight ? "Run in progress…" : "Run verification"}
          </button>
        </div>
        <p className="page-sub" style={{ marginTop: 0 }}>
          Asks this agent to exercise its authority against the live runtime. The
          request is queued here; the agent claims it over the only network path
          it has. If no agent process is running the run stays <code>PENDING</code>.
        </p>
        {runs.length === 0 && <div className="empty">No runs yet.</div>}
        {runs.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Requested</th>
                <th>Scenario</th>
                <th>Status</th>
                <th>Agent self-check</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const evaluation = run.result?.evaluation;
                return (
                  <tr key={run.id}>
                    <td className="mono">
                      {run.created_at ? new Date(run.created_at).toLocaleString() : "—"}
                    </td>
                    <td className="mono">{run.scenario}</td>
                    <td>
                      <span
                        className={
                          "badge badge-" +
                          (run.status === "COMPLETED"
                            ? "ALLOW"
                            : run.status === "FAILED"
                              ? "BLOCK"
                              : "medium")
                        }
                      >
                        {run.status}
                      </span>
                    </td>
                    <td>
                      {evaluation
                        ? `${evaluation.passed}/${evaluation.total} ${evaluation.verdict}`
                        : "—"}
                    </td>
                    <td>
                      {run.execution_id && (
                        <button
                          className="btn secondary small"
                          onClick={() => nav(`/evidence?execution=${run.execution_id}`)}
                        >
                          Open chain
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* ---- credential and revocation ---- */}
      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>Credential</h3>
        {rotated && (
          <>
            <p className="page-sub" style={{ marginTop: 0 }}>
              New token, shown once. The previous one stopped working the moment
              this was issued.
            </p>
            <div className="token-box mono">{rotated}</div>
          </>
        )}
        <div className="row" style={{ marginTop: 10 }}>
          <button
            className="btn secondary small"
            disabled={busy || revoked}
            onClick={() =>
              act(async () => {
                const result = await api.rotateAgent(agent.id);
                setRotated((result as any).token);
              })
            }
          >
            Rotate token
          </button>
          <button
            className="btn danger small"
            disabled={busy || revoked}
            onClick={() => act(() => api.revokeAgent(agent.id))}
          >
            Revoke agent
          </button>
        </div>
        <p className="page-sub">
          Revoking disables the agent and every credential it holds. Its next
          request to the gateway fails authentication — this is not a UI state.
        </p>
      </div>
    </>
  );
}
