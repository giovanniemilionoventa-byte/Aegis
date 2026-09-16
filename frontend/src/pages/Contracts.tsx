import { useEffect, useState } from "react";
import { api, type Agent, type RuntimeContract } from "../api";

/**
 * Runtime contract review.
 *
 * The contract is what the agent is *for*, and until Phase 17 it had no
 * surface at all: no API, no UI, and nothing ever created one. An operator
 * could not see, let alone govern, the control the product leads with.
 *
 * Read-only plus revoke. Authoring a contract is deliberately not a form —
 * it is a structured document, and a half-built editor would invite writing
 * one that does not say what the operator thinks it says.
 */
export default function Contracts() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [contracts, setContracts] = useState<Record<string, RuntimeContract[]>>({});
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const list = await api.agents();
      setAgents(list);
      const entries = await Promise.all(
        list.map(async (agent) => {
          try {
            return [agent.id, await api.contracts(agent.id)] as const;
          } catch {
            return [agent.id, [] as RuntimeContract[]] as const;
          }
        }),
      );
      setContracts(Object.fromEntries(entries));
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load contracts");
    }
  };

  useEffect(() => {
    load();
  }, []);

  const revoke = async (agentId: string, row: RuntimeContract) => {
    setError("");
    try {
      await api.setContractStatus(agentId, row.contract_id, row.version, "REVOKED");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "revoke failed");
    }
  };

  return (
    <>
      <h2 className="page-title">Runtime contracts</h2>
      <p className="page-sub">
        What each agent is authorised to be. An agent with no ACTIVE contract has
        no authority at all, whatever its permissions say.
      </p>
      {error && <p className="flash">{error}</p>}
      {agents.length === 0 && <div className="card"><div className="empty">No agents.</div></div>}

      {agents.map((agent) => {
        const rows = contracts[agent.id] ?? [];
        const active = rows.find((row) => row.status === "ACTIVE");
        return (
          <div className="card" key={agent.id} style={{ marginBottom: 16 }}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
              <h3 style={{ margin: 0 }}>{agent.name}</h3>
              {active ? (
                <span className="badge badge-ALLOW">governed</span>
              ) : (
                <span className="badge badge-BLOCK">no active contract — denied</span>
              )}
            </div>

            {rows.length === 0 && (
              <div className="empty">
                No contract. Every request from this agent is blocked until an
                operator writes one.
              </div>
            )}

            {rows.length > 0 && (
              <table>
                <thead>
                  <tr>
                    <th>Contract</th><th>Ver</th><th>Status</th><th>Purpose</th>
                    <th>Capabilities</th><th>Constraints</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={`${row.contract_id}-${row.version}`}>
                      <td className="mono">{row.contract_id}</td>
                      <td className="mono">{row.version}</td>
                      <td>
                        <span className={"badge badge-" + (row.status === "ACTIVE" ? "ALLOW" : "BLOCK")}>
                          {row.status}
                        </span>
                      </td>
                      <td>{row.purpose || "—"}</td>
                      <td className="mono">
                        {row.capabilities.map((cap, index) => (
                          <div key={index}>
                            {String(cap.name ?? cap.resource_kind ?? "?")}
                            {Array.isArray(cap.actions) ? ` ${(cap.actions as string[]).join("/")}` : ""}
                          </div>
                        ))}
                      </td>
                      <td className="mono">
                        {Object.keys(row.constraints ?? {}).length === 0 &&
                         Object.keys(row.data_constraints ?? {}).length === 0
                          ? "—"
                          : [
                              ...Object.keys(row.constraints ?? {}),
                              ...Object.keys(row.data_constraints ?? {}),
                            ].join(", ")}
                      </td>
                      <td>
                        {row.status === "ACTIVE" && (
                          <button
                            className="btn danger small"
                            onClick={() => revoke(agent.id, row)}
                          >
                            Revoke
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        );
      })}
    </>
  );
}
