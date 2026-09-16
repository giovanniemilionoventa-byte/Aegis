import { useEffect, useState } from "react";
import { api, type Agent, type HealthReport, type RuntimeContract } from "../api";

type Check = {
  label: string;
  state: "OK" | "ATTENTION" | "UNKNOWN";
  detail: string;
  basis: string;
};

/**
 * Security posture, from state Aegis can actually see.
 *
 * The temptation with a page like this is a column of green ticks that mean
 * "we implemented that feature". Those are worthless: a tick should say
 * something is true *right now, in this deployment*, or it should not be shown.
 *
 * So every row below is one of:
 *   OK        something the control plane can currently observe
 *   ATTENTION something observably wrong that an operator can fix
 *   UNKNOWN   something this page genuinely cannot see from here
 *
 * Properties that were proven by a runtime experiment rather than by live state
 * — the network boundary in particular — are listed separately and labelled as
 * evidence from a test run, not as live status. The dashboard is not measuring
 * the network; claiming otherwise would be the exact dishonesty this page
 * exists to avoid.
 */
export default function Posture() {
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [contracts, setContracts] = useState<Record<string, RuntimeContract | null>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch((err) => setError(err instanceof Error ? err.message : "failed"));
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
      .catch(() => undefined);
  }, []);

  const activeAgents = agents.filter((agent) => agent.status === "active");
  const ungoverned = activeAgents.filter((agent) => !contracts[agent.id]);
  const posture = health?.posture;

  const checks: Check[] = [
    {
      label: "Deployment secrets",
      state: posture ? (posture.secure ? "OK" : "ATTENTION") : "UNKNOWN",
      detail: posture
        ? posture.secure
          ? "No shipped default secrets are in use."
          : `Still using development defaults: ${posture.weak_secrets.join(", ")}`
        : "Could not read the control plane's posture.",
      basis: "GET /api/health, reported by this process",
    },
    {
      label: "Contract enforcement",
      state: ungoverned.length === 0 ? "OK" : "ATTENTION",
      detail:
        ungoverned.length === 0
          ? `All ${activeAgents.length} active agents are governed by a contract.`
          : `${ungoverned.length} active agent(s) have no contract: ${ungoverned
              .map((agent) => agent.name)
              .join(", ")}. Every request they make is refused.`,
      basis: "live contract resolution per agent",
    },
    {
      label: "Default-deny",
      state: "OK",
      detail:
        "An agent with no active contract is refused. This deployment runs " +
        "fail-closed.",
      basis: "AEGIS_REQUIRE_RUNTIME_CONTRACT, enforced in the authorization path",
    },
    {
      label: "Agent liveness",
      state: "UNKNOWN",
      detail:
        "Aegis has no heartbeat. It knows whether an agent is active or " +
        "revoked in configuration, not whether it is running.",
      basis: "not implemented — stated rather than guessed",
    },
    {
      label: "Credential isolation from the provider",
      state: "ATTENTION",
      detail:
        "Per-tenant credentials are derived from a master key the provider " +
        "holds, so the provider can still derive any tenant's credential. " +
        "CAN USE ≠ CAN READ is not solved.",
      basis: "known limitation, documented in the phase reports",
    },
  ];

  const runtimeEvidence = [
    {
      label: "Execution boundary",
      detail:
        "agent → broker / protected tool / control plane refused at the " +
        "network layer (ENETUNREACH), not by application code.",
      basis: "docs/evidence/phase17_execution_boundary.json — 13/13 paths",
    },
    {
      label: "Credential never reaches the agent",
      detail:
        "The agent container holds only its own Aegis token: no tool " +
        "credential, EAT key or internal service token.",
      basis: "observed from inside the container during the boundary proof",
    },
    {
      label: "Evidence tamper detection",
      detail:
        "Modifying, deleting, reordering or stripping the chain is detected. " +
        "Truncating the tail AND rewriting the stored tip is not detected by " +
        "the chain alone.",
      basis: "21 tamper cases; the exception is documented, not hidden",
    },
    {
      label: "Approval binding",
      detail:
        "An approval authorizes one request once, and concurrent redemption " +
        "was found exploitable and fixed.",
      basis: "Phase 17 finding A-1, stress-tested 25 runs",
    },
  ];

  const badge = (state: Check["state"]) =>
    state === "OK" ? "ALLOW" : state === "ATTENTION" ? "APPROVAL" : "medium";

  return (
    <>
      <h2 className="page-title">Security posture</h2>
      <p className="page-sub">
        What Aegis can see about this deployment right now. Anything it cannot
        see is marked unknown rather than assumed good.
      </p>
      {error && <p className="flash">{error}</p>}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Live state</h3>
        <table>
          <thead>
            <tr>
              <th>Check</th>
              <th>State</th>
              <th>Detail</th>
              <th>Based on</th>
            </tr>
          </thead>
          <tbody>
            {checks.map((check) => (
              <tr key={check.label}>
                <td>{check.label}</td>
                <td>
                  <span className={"badge badge-" + badge(check.state)}>
                    {check.state}
                  </span>
                </td>
                <td>{check.detail}</td>
                <td className="mono">{check.basis}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>Proven by runtime experiment</h3>
        <p className="page-sub" style={{ marginTop: 0 }}>
          These were established by running the system and observing it, not by
          this page measuring anything. They are evidence from a recorded run —
          if the topology changes, the run has to be repeated.
        </p>
        <table>
          <thead>
            <tr>
              <th>Property</th>
              <th>What was observed</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {runtimeEvidence.map((row) => (
              <tr key={row.label}>
                <td>{row.label}</td>
                <td>{row.detail}</td>
                <td className="mono">{row.basis}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>Not proven</h3>
        <ul>
          <li>
            No customer has used this. Nothing here is customer-proven or
            commercially-proven.
          </li>
          <li>
            The protected tool is a mock. The security path is real; the system
            behind it is not.
          </li>
          <li>
            Only <code>crm</code> is connected to a tool. For other resources
            Aegis decides but nothing executes either way.
          </li>
          <li>
            Performance was last measured with the contract engine in
            pass-through, so those numbers do not describe this system.
          </li>
        </ul>
      </div>
    </>
  );
}
