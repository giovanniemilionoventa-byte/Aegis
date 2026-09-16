import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Agent, type RuntimeContract } from "../api";

/**
 * Agent registry.
 *
 * This replaces the inline create-and-configure form that used to live here.
 * Creating an agent and granting it authority are now one guided flow (see
 * NewAgent), because doing them separately made it easy to leave an agent
 * half-configured and then wonder why every request was refused.
 *
 * The "Authority" column is the useful one: an agent without an ACTIVE contract
 * has none, whatever its permissions say.
 */
export default function Agents() {
  const nav = useNavigate();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [contracts, setContracts] = useState<Record<string, RuntimeContract | null>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .agents()
      .then(async (list) => {
        setAgents(list);
        const entries = await Promise.all(
          list.map(async (agent) => {
            try {
              return [agent.id, await api.activeContract(agent.id)] as const;
            } catch {
              return [agent.id, null] as const;
            }
          }),
        );
        setContracts(Object.fromEntries(entries));
      })
      .catch((err) => setError(err instanceof Error ? err.message : "failed"));
  }, []);

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <h2 className="page-title">Agents</h2>
        <button className="btn" onClick={() => nav("/agents/new")}>
          Add agent
        </button>
      </div>
      <p className="page-sub">
        Identity is distinct from the human owner and from the model provider. An
        agent has no authority until an operator gives it a contract.
      </p>
      {error && <p className="flash">{error}</p>}

      <div className="card">
        {agents.length === 0 && <div className="empty">No agents yet.</div>}
        {agents.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Kind</th>
                <th>State</th>
                <th>Authority</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {agents.map((agent) => {
                const active = contracts[agent.id];
                const revoked = agent.status !== "active";
                const verification = agent.provider === "aegis-reference";
                return (
                  <tr key={agent.id}>
                    <td>
                      <Link to={`/agents/${agent.id}`}>{agent.name}</Link>
                    </td>
                    <td>
                      {verification ? (
                        <span className="badge badge-medium">verification harness</span>
                      ) : (
                        <span className="mono">{agent.provider}</span>
                      )}
                    </td>
                    <td>
                      <span className={"badge badge-" + (revoked ? "BLOCK" : "ALLOW")}>
                        {revoked ? "revoked" : "active"}
                      </span>
                    </td>
                    <td>
                      {revoked ? (
                        <span className="badge badge-BLOCK">none</span>
                      ) : active ? (
                        <span className="mono">
                          {active.contract_id} v{active.version}
                        </span>
                      ) : (
                        <span className="badge badge-BLOCK">no contract — denied</span>
                      )}
                    </td>
                    <td>
                      <button
                        className="btn secondary small"
                        onClick={() => nav(`/agents/${agent.id}`)}
                      >
                        Open
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <p className="page-sub" style={{ marginTop: 14 }}>
        An agent marked <em>verification harness</em> runs inside the Aegis agent
        container and executes a fixed scenario on request. It is a test harness,
        not a production agent.
      </p>
    </>
  );
}
