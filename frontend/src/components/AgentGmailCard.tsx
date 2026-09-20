import { useCallback, useEffect, useState } from "react";
import {
  api,
  type AgentGmailStatus,
  type GmailStatus,
  type SimulationResult,
} from "../api";

/**
 * Gmail, on the page where an operator is actually configuring an agent.
 *
 * Two separate facts live here and are deliberately not merged, because
 * merging them is how an operator ends up believing an agent can send mail
 * when it cannot:
 *
 *   1. Is a mailbox connected for this organization? One connection, one
 *      credential, shared by the tenant.
 *   2. Has THIS agent been granted access to it? Connecting a mailbox grants
 *      nothing; access is per agent.
 *
 * The operation table is not a static list of what Phase 19 supports. Each row
 * is the answer the real engines give for THIS agent right now, fetched from
 * the simulator, which applies the same permission, policy, contract and
 * mailbox-access checks the gateway does. If an operator misconfigures
 * something, this table says so instead of showing the intended posture.
 */

const OPERATIONS = ["SEARCH", "READ", "DRAFT", "SEND", "DELETE"] as const;

type Row = { action: string; decision: string; reason: string };

function mark(decision: string) {
  if (decision === "ALLOW") return { glyph: "✓", cls: "badge-ALLOW", label: "Allowed" };
  if (decision === "APPROVAL")
    return { glyph: "⚠", cls: "badge-APPROVAL", label: "Human approval required" };
  return { glyph: "✕", cls: "badge-BLOCK", label: "Denied" };
}

export default function AgentGmailCard({
  agentId,
  agentRevoked,
}: {
  agentId: string;
  agentRevoked: boolean;
}) {
  const [tenant, setTenant] = useState<GmailStatus | null>(null);
  const [agent, setAgent] = useState<AgentGmailStatus | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [tenantStatus, agentStatus] = await Promise.all([
        api.gmailStatus(),
        api.agentGmail(agentId),
      ]);
      setTenant(tenantStatus);
      setAgent(agentStatus);
      setError("");

      const results = await Promise.all(
        OPERATIONS.map(async (action) => {
          try {
            const simulated: SimulationResult = await api.simulate(agentId, {
              resource_kind: "gmail",
              action,
              scope: "mailbox",
            });
            return {
              action,
              decision: simulated.decision,
              reason: simulated.reason,
            };
          } catch {
            return { action, decision: "UNKNOWN", reason: "Could not be evaluated." };
          }
        }),
      );
      setRows(results);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load Gmail status");
    }
  }, [agentId]);

  useEffect(() => {
    load();
  }, [load]);

  const connect = async () => {
    // Navigate to Aegis, not to Google. The server builds the consent URL and
    // answers 302, so this page never handles the Google client id. The address
    // carries a one-use ticket, never the session token.
    try {
      const started = await api.gmailConnect();
      window.location.href = started.authorization_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
    }
  };

  const setGrant = async (grant: boolean) => {
    setBusy(true);
    try {
      if (grant) await api.grantAgentGmail(agentId);
      else await api.revokeAgentGmail(agentId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
    } finally {
      setBusy(false);
    }
  };

  const unconfigured =
    tenant && (!tenant.oauth_client_configured || !tenant.encryption_key_configured);

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <h3 style={{ margin: 0 }}>Gmail</h3>
        {agent && (
          <span className={"badge " + (agent.allowed ? "badge-ALLOW" : "badge-BLOCK")}>
            {agent.allowed ? "ready" : "not ready"}
          </span>
        )}
      </div>

      {error && <p className="flash">{error}</p>}

      {unconfigured && (
        <p className="page-sub">
          Gmail is not set up in this deployment yet. That is a one-time
          administrator step — see <code>docs/PHASE_19_GMAIL.md</code>.
        </p>
      )}

      {tenant && !unconfigured && (
        <table>
          <tbody>
            <tr>
              <td style={{ width: "40%" }}>Mailbox (organization)</td>
              <td>
                {tenant.connected ? (
                  <>
                    <span className="badge badge-ALLOW">connected</span>{" "}
                    <span className="mono">
                      {tenant.connection?.google_email || "(address unavailable)"}
                    </span>
                  </>
                ) : (
                  <>
                    <span className="badge badge-BLOCK">not connected</span>{" "}
                    <button
                      className="btn small"
                      style={{ marginLeft: 8 }}
                      onClick={connect}
                    >
                      Connect Gmail
                    </button>
                  </>
                )}
              </td>
            </tr>
            <tr>
              <td>Access for this agent</td>
              <td>
                {agent?.granted ? (
                  <>
                    <span className="badge badge-ALLOW">granted</span>{" "}
                    <button
                      className="btn secondary small"
                      style={{ marginLeft: 8 }}
                      disabled={busy}
                      onClick={() => setGrant(false)}
                    >
                      Revoke access
                    </button>
                  </>
                ) : (
                  <>
                    <span className="badge badge-BLOCK">not granted</span>{" "}
                    <button
                      className="btn small"
                      style={{ marginLeft: 8 }}
                      disabled={busy || !tenant.connected || agentRevoked}
                      onClick={() => setGrant(true)}
                    >
                      Grant access
                    </button>
                  </>
                )}
              </td>
            </tr>
          </tbody>
        </table>
      )}

      {agent && !agent.allowed && (
        <p className="page-sub">{agent.reason}</p>
      )}

      {tenant?.connected && (
        <p className="page-sub" style={{ marginTop: 12, marginBottom: 4 }}>
          Connecting a mailbox grants no agent access to it. Each agent is
          granted separately, and disconnecting revokes every grant.
        </p>
      )}

      <h4 style={{ marginBottom: 4 }}>What this agent may do</h4>
      <p className="page-sub" style={{ marginTop: 0 }}>
        Evaluated now, by the same permission, policy, contract and
        mailbox-access checks the gateway applies. Not a description of the
        intended setup — the actual answer for this agent.
      </p>
      <table>
        <tbody>
          {rows.map((row) => {
            const state = mark(row.decision);
            return (
              <tr key={row.action}>
                <td style={{ width: "1%", paddingRight: 8 }}>{state.glyph}</td>
                <td className="mono" style={{ width: "30%" }}>
                  gmail.{row.action.toLowerCase()}
                </td>
                <td style={{ width: "25%" }}>
                  <span className={"badge " + state.cls}>{state.label}</span>
                </td>
                <td className="page-sub">{row.reason}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
