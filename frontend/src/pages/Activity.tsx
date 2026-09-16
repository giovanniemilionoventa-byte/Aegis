import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Agent, type EventRow, type ExecutionRow } from "../api";

/**
 * What the agents actually attempted, and what Aegis decided.
 *
 * Grouped by execution rather than listed flat, because a single decision is
 * rarely the interesting thing — the story is "it read, then tried to delete,
 * then asked to update and waited for a person". A flat log hides that.
 *
 * Every row here is a real authorization event written by the gateway. Nothing
 * is synthesised to fill the page: an empty state means no agent has done
 * anything yet, and says so.
 */
export default function Activity() {
  const nav = useNavigate();
  const [events, setEvents] = useState<EventRow[]>([]);
  const [executions, setExecutions] = useState<ExecutionRow[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [filter, setFilter] = useState<string>("ALL");
  const [error, setError] = useState("");

  const load = () =>
    Promise.all([api.events(), api.executions(), api.agents()])
      .then(([e, x, a]) => {
        setEvents(e);
        setExecutions(x);
        setAgents(a);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "failed"));

  useEffect(() => {
    load();
  }, []);

  const agentName = (id: string | null) =>
    agents.find((agent) => agent.id === id)?.name ?? (id ? id.slice(0, 8) : "—");

  const visible = events.filter((event) =>
    filter === "ALL" ? true : event.decision === filter,
  );

  const byExecution = new Map<string, EventRow[]>();
  visible.forEach((event) => {
    const key = event.execution_id ?? "unattached";
    byExecution.set(key, [...(byExecution.get(key) ?? []), event]);
  });
  const ordered = [...byExecution.entries()].sort((a, b) => {
    const left = a[1][0]?.created_at ?? "";
    const right = b[1][0]?.created_at ?? "";
    return right.localeCompare(left);
  });

  const counts = {
    ALLOW: events.filter((e) => e.decision === "ALLOW").length,
    APPROVAL: events.filter((e) => e.decision === "APPROVAL").length,
    BLOCK: events.filter((e) => e.decision === "BLOCK").length,
  };

  return (
    <>
      <h2 className="page-title">Activity</h2>
      <p className="page-sub">
        Every action an agent attempted, and the decision Aegis made. Grouped by
        execution so a sequence reads as one story.
      </p>
      {error && <p className="flash">{error}</p>}

      <div className="card">
        <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
          {(["ALL", "ALLOW", "APPROVAL", "BLOCK"] as const).map((option) => (
            <button
              key={option}
              className={"btn small" + (filter === option ? "" : " secondary")}
              onClick={() => setFilter(option)}
            >
              {option === "ALL"
                ? `All (${events.length})`
                : `${option} (${counts[option as keyof typeof counts]})`}
            </button>
          ))}
          <button className="btn secondary small" onClick={load}>
            Refresh
          </button>
        </div>
      </div>

      {ordered.length === 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="empty">
            No activity yet. Open an agent and run a verification to see Aegis
            make real decisions.
          </div>
        </div>
      )}

      {ordered.map(([executionId, rows]) => {
        const execution = executions.find((row) => row.id === executionId);
        const sorted = [...rows].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
        return (
          <div className="card" style={{ marginTop: 16 }} key={executionId}>
            <div
              className="row"
              style={{ justifyContent: "space-between", alignItems: "baseline" }}
            >
              <h3 style={{ margin: 0 }} className="mono">
                {executionId === "unattached" ? "Unattached" : executionId.slice(0, 28)}
              </h3>
              <div className="row">
                <span className="mono">{agentName(sorted[0]?.agent_id ?? null)}</span>
                {execution && executionId !== "unattached" && (
                  <button
                    className="btn secondary small"
                    onClick={() => nav(`/evidence?execution=${executionId}`)}
                  >
                    Evidence
                  </button>
                )}
              </div>
            </div>
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Action</th>
                  <th>Decision</th>
                  <th>Executed</th>
                  <th>Risk</th>
                  <th>Why</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((event) => {
                  // An ALLOW recorded by the gateway is an action it carried
                  // out; APPROVAL and BLOCK never reach the protected tool.
                  const executed = event.decision === "ALLOW";
                  return (
                    <tr key={event.id}>
                      <td className="mono">{event.seq ?? "—"}</td>
                      <td className="mono">
                        {event.resource_kind}.{event.action}
                        <br />
                        {event.scope}
                        {event.destination ? ` → ${event.destination}` : ""}
                      </td>
                      <td>
                        <span className={"badge badge-" + event.decision}>
                          {event.decision === "APPROVAL" ? "NEEDS A HUMAN" : event.decision}
                        </span>
                      </td>
                      <td>
                        {executed ? (
                          <span className="badge badge-ALLOW">executed</span>
                        ) : (
                          <span className="badge badge-BLOCK">not executed</span>
                        )}
                      </td>
                      <td>
                        <span className={"badge badge-" + event.risk_level}>
                          {event.risk_level}
                        </span>
                      </td>
                      <td>{event.reason}</td>
                      <td className="mono">
                        {new Date(event.created_at).toLocaleTimeString()}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
      })}
    </>
  );
}
