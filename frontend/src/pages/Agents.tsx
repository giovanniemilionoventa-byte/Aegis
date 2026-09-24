import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Agent, type RuntimeContract } from "../api";
import { useT } from "../i18n";
import { timeAgo } from "../time";

/**
 * Agent registry.
 *
 * "Connected" here means the agent has called Aegis with its key at least once,
 * and the time says when. It is not a heartbeat: Aegis cannot know the agent is
 * running right now, so the page never says so.
 *
 * The "Permissions" column is the useful one: an agent without an ACTIVE
 * contract has none, whatever its permission rows say.
 */
export default function Agents() {
  const t = useT();
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [contracts, setContracts] = useState<Record<string, RuntimeContract | null>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    let loadedContracts = false;

    const load = async () => {
      try {
        const list = await api.agents();
        if (!live) return;
        setAgents(list);
        if (loadedContracts) return;
        loadedContracts = true;
        const entries = await Promise.all(
          list.map(async (agent) => {
            try {
              return [agent.id, await api.activeContract(agent.id)] as const;
            } catch {
              return [agent.id, null] as const;
            }
          }),
        );
        if (live) setContracts(Object.fromEntries(entries));
      } catch (err) {
        if (live) setError(err instanceof Error ? err.message : t("common.failed"));
      }
    };

    load();
    // The first call from a new agent is the moment this list is waiting for.
    const timer = window.setInterval(() => {
      if (!document.hidden) load();
    }, 8000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [t]);

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <h1 className="page-title">{t("agents.title")}</h1>
        <Link className="btn" to="/agents/new">
          {t("agents.add")}
        </Link>
      </div>
      <p className="page-sub">{t("agents.lead")}</p>
      {error && (
        <p className="flash" role="alert">
          {error}
        </p>
      )}

      <div className="card">
        {agents === null && !error && <div className="empty">{t("common.loading")}</div>}
        {agents !== null && agents.length === 0 && (
          <div className="empty">
            <p>{t("agents.empty")}</p>
            <Link className="btn large" to="/agents/new">
              {t("agents.add")}
            </Link>
          </div>
        )}
        {agents !== null && agents.length > 0 && (
          <table>
            <caption className="visually-hidden">{t("agents.title")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("agents.colName")}</th>
                <th scope="col">{t("agents.colStatus")}</th>
                <th scope="col">{t("agents.colAuthority")}</th>
              </tr>
            </thead>
            <tbody>
              {agents.map((agent) => {
                const active = contracts[agent.id];
                const revoked = agent.status !== "active";
                return (
                  <tr key={agent.id}>
                    <th scope="row" style={{ fontWeight: 600 }}>
                      <Link to={`/agents/${agent.id}`}>{agent.name}</Link>
                      {agent.provider === "aegis-reference" && (
                        <>
                          {" "}
                          <span className="badge badge-medium">{t("agents.testAgent")}</span>
                        </>
                      )}
                    </th>
                    <td>
                      {revoked ? (
                        <span className="status off">
                          <span className="dot" aria-hidden="true" />
                          {t("agents.revoked")}
                        </span>
                      ) : agent.last_seen_at ? (
                        <>
                          <span className="status ok">
                            <span className="dot" aria-hidden="true" />
                            {t("agents.connected")}
                          </span>
                          <div className="hint">
                            {t("agents.lastCall", { when: timeAgo(agent.last_seen_at) })}
                          </div>
                        </>
                      ) : (
                        <span className="status wait">
                          <span className="dot" aria-hidden="true" />
                          {t("agents.waitingFirst")}
                        </span>
                      )}
                    </td>
                    <td>
                      {revoked ? (
                        "—"
                      ) : active ? (
                        <span className="mono">
                          {active.contract_id} v{active.version}
                        </span>
                      ) : (
                        <span className="badge badge-BLOCK">{t("agents.noContract")}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
