import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import AgentGmailCard from "../components/AgentGmailCard";
import ConnectCard from "../components/ConnectCard";
import {
  api,
  type Agent,
  type Permission,
  type RuntimeContract,
  type VerificationRun,
} from "../api";
import { capLabel, useT } from "../i18n";
import { formatDateTime, timeAgo } from "../time";

/**
 * One agent, showing real backend state and nothing else.
 *
 * Two honesty rules this page follows:
 *
 *   * "Last contact" is the last call Aegis received with this agent's key. It
 *     is not liveness: Aegis has no heartbeat and cannot tell whether the agent
 *     is running right now, and the page says so instead of showing a green dot
 *     that would mean more than it does.
 *
 *   * A run's status describes the request, not the security outcome. Whether
 *     each action was allowed or refused comes from the evidence chain the
 *     gateway wrote while authorizing it, which is linked, not restated.
 *
 * The Gmail card and the verification harness only appear on deployments that
 * have them: Gmail is a pilot-only connector, and the harness needs the demo
 * agent container, which a hosted deployment does not run.
 */
export default function AgentDetail() {
  const t = useT();
  const { agentId = "" } = useParams();
  const nav = useNavigate();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [contracts, setContracts] = useState<RuntimeContract[]>([]);
  const [runs, setRuns] = useState<VerificationRun[]>([]);
  const [features, setFeatures] = useState<{ gmail?: boolean; demo?: boolean }>({});
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
      setError(err instanceof Error ? err.message : t("common.failed"));
    }
  }, [agentId, t]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    api.health().then((health) => setFeatures(health.features ?? {})).catch(() => {});
  }, []);

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
      setError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setBusy(false);
    }
  };

  if (!agent) {
    return (
      <>
        <h1 className="page-title">{t("nav.agents")}</h1>
        {error ? (
          <p className="flash" role="alert">
            {error}
          </p>
        ) : (
          <div className="card">
            <div className="empty">{t("common.loading")}</div>
          </div>
        )}
      </>
    );
  }

  const active = contracts.find((row) => row.status === "ACTIVE");
  const revoked = agent.status !== "active";
  const approvalRules = (active?.approval_rules ?? []) as Array<Record<string, any>>;
  const needsHuman = new Set(
    approvalRules.map((rule) => `${rule.resource_kind ?? "*"}.${rule.action ?? "*"}`),
  );

  const rotate = () => {
    if (!window.confirm(t("ad.confirmRotate"))) return;
    act(async () => {
      const result = await api.rotateAgent(agent.id);
      setRotated(result.token);
    });
  };

  return (
    <>
      <h1 className="page-title">{agent.name}</h1>
      <p className="page-sub">{agent.description || t("ad.noDescription")}</p>
      {error && (
        <p className="flash" role="alert">
          {error}
        </p>
      )}

      {/* ---- state ---- */}
      <section className="card">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
          <h2 style={{ margin: 0 }}>{t("agents.colStatus")}</h2>
          {revoked ? (
            <span className="badge badge-BLOCK">{t("ad.revoked")}</span>
          ) : active ? (
            <span className="badge badge-ALLOW">{t("ad.governed")}</span>
          ) : (
            <span className="badge badge-BLOCK">{t("ad.noContractDenied")}</span>
          )}
        </div>
        <table>
          <tbody>
            <tr>
              <th scope="row">{t("ad.lastContact")}</th>
              <td>
                {agent.last_seen_at
                  ? `${formatDateTime(agent.last_seen_at)} (${timeAgo(agent.last_seen_at)})`
                  : t("ad.never")}
              </td>
            </tr>
            <tr>
              <th scope="row">{t("ad.id")}</th>
              <td className="mono">{agent.id}</td>
            </tr>
            <tr>
              <th scope="row">{t("ad.created")}</th>
              <td>{formatDateTime(agent.created_at)}</td>
            </tr>
            {agent.revoked_at && (
              <tr>
                <th scope="row">{t("ad.revokedAt")}</th>
                <td>{formatDateTime(agent.revoked_at)}</td>
              </tr>
            )}
          </tbody>
        </table>
        <p className="hint">{t("ad.lastNote")}</p>
      </section>

      {/* ---- how to point a real agent at Aegis ---- */}
      {!revoked && (
        <ConnectCard
          agentId={agent.id}
          token={rotated}
          onRotate={rotate}
          rotating={busy}
        />
      )}

      {/* ---- capabilities ---- */}
      <section className="card" style={{ marginTop: 16 }}>
        <h2>{t("ad.caps")}</h2>
        {permissions.length === 0 && <div className="empty">{t("ad.noPerms")}</div>}
        {permissions.length > 0 && (
          <table>
            <caption className="visually-hidden">{t("ad.caps")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("ad.colCap")}</th>
                <th scope="col">{t("ad.colScope")}</th>
                <th scope="col">{t("ad.colEffect")}</th>
                <th scope="col">{t("ad.colContract")}</th>
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
                    <th scope="row" style={{ fontWeight: 600 }}>
                      {capLabel(perm.resource_kind, perm.action)}
                      <div className="hint mono">
                        {perm.resource_kind}.{perm.action}
                      </div>
                    </th>
                    <td className="mono">{perm.scope}</td>
                    <td>
                      {human ? (
                        <span className="badge badge-APPROVAL">{t("ad.human")}</span>
                      ) : perm.effect === "allow" ? (
                        <span className="badge badge-ALLOW">{t("ad.allow")}</span>
                      ) : (
                        <span className="badge badge-BLOCK">{t("ad.deny")}</span>
                      )}
                    </td>
                    <td>
                      {inContract ? (
                        <span className="badge badge-ALLOW">{t("ad.inContract")}</span>
                      ) : (
                        <span className="badge badge-BLOCK">{t("ad.notInContract")}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {permissions.length > 0 && <p className="hint">{t("policy.note")}</p>}
      </section>

      {/* ---- Gmail, on the page where the agent is configured (pilot deployments) ---- */}
      {features.gmail && <AgentGmailCard agentId={agent.id} agentRevoked={revoked} />}

      {/* ---- contract ---- */}
      <section className="card" style={{ marginTop: 16 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
          <h2 style={{ margin: 0 }}>{t("ad.contract")}</h2>
          {active && (
            <button
              className="btn danger small"
              disabled={busy}
              onClick={() =>
                act(() =>
                  api.setContractStatus(agent.id, active.contract_id, active.version, "REVOKED"),
                )
              }
            >
              {t("ad.revokeContract")}
            </button>
          )}
        </div>
        {contracts.length === 0 && <div className="empty">{t("ad.noContracts")}</div>}
        {contracts.length > 0 && (
          <table>
            <caption className="visually-hidden">{t("ad.contract")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("ad.colContractId")}</th>
                <th scope="col">{t("ad.colVersion")}</th>
                <th scope="col">{t("ad.colStatus")}</th>
                <th scope="col">{t("ad.colPurpose")}</th>
                <th scope="col">{t("ad.colExpires")}</th>
              </tr>
            </thead>
            <tbody>
              {contracts.map((row) => (
                <tr key={`${row.contract_id}-${row.version}`}>
                  <td className="mono">{row.contract_id}</td>
                  <td className="mono">{row.version}</td>
                  <td>
                    <span className={"badge badge-" + (row.status === "ACTIVE" ? "ALLOW" : "BLOCK")}>
                      {row.status}
                    </span>
                  </td>
                  <td>{row.purpose || "—"}</td>
                  <td>{row.expires_at ? formatDateTime(row.expires_at) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* ---- runtime verification: needs the demo agent container ---- */}
      {features.demo && (
        <section className="card" style={{ marginTop: 16 }}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
            <h2 style={{ margin: 0 }}>Runtime verification (demo agent)</h2>
            <button
              className="btn small"
              disabled={busy || revoked || inFlight}
              onClick={() => act(() => api.requestRun(agent.id, "canonical"))}
            >
              {inFlight ? "Run in progress…" : "Run verification"}
            </button>
          </div>
          <p className="page-sub" style={{ marginTop: 0 }}>
            Asks this agent to exercise its authority against the live runtime. The request is
            queued here; the agent claims it over the only network path it has. If no agent process
            is running the run stays <code>PENDING</code>.
          </p>
          {runs.length === 0 && <div className="empty">No runs yet.</div>}
          {runs.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th scope="col">Requested</th>
                  <th scope="col">Scenario</th>
                  <th scope="col">Status</th>
                  <th scope="col">Agent self-check</th>
                  <th scope="col">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => {
                  const evaluation = run.result?.evaluation;
                  return (
                    <tr key={run.id}>
                      <td>{formatDateTime(run.created_at)}</td>
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
        </section>
      )}

      {/* ---- revocation ---- */}
      <section className="card" style={{ marginTop: 16 }}>
        <h2>{t("ad.revokeAgent")}</h2>
        <p className="page-sub">{t("ad.revokeNote")}</p>
        <button
          className="btn danger"
          disabled={busy || revoked}
          onClick={() => {
            if (window.confirm(t("ad.confirmRevoke"))) act(() => api.revokeAgent(agent.id));
          }}
        >
          {t("ad.revokeAgent")}
        </button>
      </section>
    </>
  );
}
