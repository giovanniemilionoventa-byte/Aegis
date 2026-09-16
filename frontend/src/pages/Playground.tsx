import { FormEvent, useEffect, useState } from "react";
import { api, type Agent, type SimulationResult } from "../api";

/**
 * "What would Aegis decide?" — checked before an agent depends on it.
 *
 * This page used to offer a "send through the enforcement gateway" checkbox
 * that could not work. The gateway sits on internal networks with no host port,
 * so the browser has no route to it, and neither does the control plane this
 * dashboard talks to. Even `/api/authorize` is a gateway route: from here it
 * is a 404 that looks like a policy result.
 *
 * So the page now asks the control plane to evaluate the request with the same
 * engines the gateway uses, and says plainly what that answer is and is not:
 * advisory, writes nothing, and does not model trajectory or workflow.
 */
export default function Playground() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentId, setAgentId] = useState("");
  const [resource, setResource] = useState("crm");
  const [action, setAction] = useState("READ");
  const [scope, setScope] = useState("customers");
  const [destination, setDestination] = useState("");
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .agents()
      .then((list) => {
        setAgents(list);
        if (list.length && !agentId) setAgentId(list[0].id);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "failed"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setResult(null);
    try {
      setResult(
        await api.simulate(agentId, {
          resource_kind: resource,
          action,
          scope,
          destination: destination || null,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
    }
  }

  const badgeFor = (outcome: string) =>
    outcome === "PASS" || outcome === "ALLOW"
      ? "ALLOW"
      : outcome === "APPROVAL"
        ? "APPROVAL"
        : outcome === "SKIPPED"
          ? "medium"
          : "BLOCK";

  return (
    <>
      <h2 className="page-title">Check a decision</h2>
      <p className="page-sub">
        Evaluates a request against an agent's real permissions, policies and
        contract, so you can confirm a contract says what you meant before an
        agent depends on it.
      </p>

      <div className="card">
        <form onSubmit={submit} className="grid" style={{ gap: 10, maxWidth: 620 }}>
          <label className="field">
            Agent
            <select
              id="sim-agent"
              value={agentId}
              onChange={(event) => setAgentId(event.target.value)}
            >
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
            </select>
          </label>
          <div className="row">
            <input id="sim-resource" value={resource} onChange={(e) => setResource(e.target.value)} />
            <input id="sim-action" value={action} onChange={(e) => setAction(e.target.value)} />
            <input id="sim-scope" value={scope} onChange={(e) => setScope(e.target.value)} />
            <input
              id="sim-destination"
              value={destination}
              onChange={(e) => setDestination(e.target.value)}
              placeholder="destination (optional)"
            />
          </div>
          <button className="btn" type="submit" disabled={!agentId}>
            Evaluate
          </button>
        </form>

        {error && <p className="flash">{error}</p>}

        {result && (
          <div style={{ marginTop: 16 }}>
            <span className={"badge badge-" + result.decision}>
              {result.decision === "APPROVAL" ? "NEEDS A HUMAN" : result.decision}
            </span>{" "}
            <span className={"badge badge-" + result.risk_level}>
              {result.risk_level} {result.risk_score}
            </span>
            <p>{result.reason}</p>
            <table>
              <thead>
                <tr>
                  <th>Layer</th>
                  <th>Outcome</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {result.steps.map((step, index) => (
                  <tr key={index}>
                    <td className="mono">{step.layer}</td>
                    <td>
                      <span className={"badge badge-" + badgeFor(step.outcome)}>
                        {step.outcome}
                      </span>
                    </td>
                    <td>{step.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="page-sub">{result.note}</p>
          </div>
        )}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>This does not execute anything</h3>
        <p className="page-sub" style={{ marginTop: 0 }}>
          Executing a protected action means reaching the enforcement gateway,
          which sits on internal networks with no host port. The browser has no
          route to it, and neither does the control plane. That separation is the
          deployment boundary, and it is what stops a compromised dashboard from
          driving an agent.
        </p>
        <p className="page-sub">
          To watch a real execution, open an agent and use{" "}
          <strong>Runtime verification</strong>: the request is queued for the
          agent, and the agent reaches the gateway from inside the agent network.
        </p>
      </div>
    </>
  );
}
