import { useEffect, useState } from "react";
import { api, type AgentSetup } from "../api";

/**
 * How to point a real AI agent at Aegis.
 *
 * An operator who has just created an agent has to connect their own runtime
 * to it, and until now the dashboard said nothing about how — the gap was
 * filled by reading the source, which is not a product.
 *
 * What this shows is the address, the header, the request shape and the
 * operations this agent may actually name. What it does not show, and will not:
 * the agent's token (Aegis holds only a hash and genuinely cannot re-display
 * it), any Gmail or OAuth credential, and any internal address. This card is
 * exactly where someone would be tempted to leak one for convenience.
 */
export default function AgentSetupCard({ agentId }: { agentId: string }) {
  const [setup, setSetup] = useState<AgentSetup | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .agentSetup(agentId)
      .then(setSetup)
      .catch((err) => setError(err instanceof Error ? err.message : "failed"));
  }, [agentId]);

  if (error) return <div className="card" style={{ marginTop: 16 }}><p className="flash">{error}</p></div>;
  if (!setup) return null;

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3 style={{ marginTop: 0 }}>Connect your agent</h3>
      <p className="page-sub" style={{ marginTop: 0 }}>
        Your agent talks to one address and presents one credential. It never
        receives a credential for any protected system.
      </p>

      <table>
        <tbody>
          <tr>
            <td style={{ width: "30%" }}>Endpoint</td>
            <td className="mono">
              {"$" + setup.gateway_base_url_env}
              {setup.gateway_path_pattern}
            </td>
          </tr>
          <tr>
            <td>Auth header</td>
            <td className="mono">{setup.auth_header}: &lt;agent token&gt;</td>
          </tr>
          <tr>
            <td>Token</td>
            <td className="page-sub">{setup.credential.note}</td>
          </tr>
        </tbody>
      </table>

      <h4 style={{ marginBottom: 4 }}>Operations this agent may request</h4>
      {setup.operations.length === 0 ? (
        <p className="page-sub" style={{ marginTop: 0 }}>
          None. This agent holds no permission for a connected tool, so every
          request it makes is refused.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Operation</th>
              <th>Path</th>
              <th>Scope</th>
            </tr>
          </thead>
          <tbody>
            {setup.operations.map((op) => (
              <tr key={op.canonical}>
                <td className="mono">{op.canonical}</td>
                <td className="mono">{op.path}</td>
                <td className="mono">{op.scope}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4 style={{ marginBottom: 4 }}>Request body</h4>
      <pre className="mono token-box" style={{ whiteSpace: "pre-wrap" }}>
        {JSON.stringify(setup.request_shape, null, 2)}
      </pre>

      <h4 style={{ marginBottom: 4 }}>Never given to an agent</h4>
      <ul className="page-sub" style={{ marginTop: 0 }}>
        {setup.never_supplied_to_agents.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
