import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Capability } from "../api";

type Disposition = "ALLOW" | "APPROVAL" | "DENY";

/**
 * Agent onboarding.
 *
 * Every choice here becomes real backend state, in three places that already
 * existed and are authoritative:
 *
 *   ALLOW     -> a Permission for the agent, and the capability in its contract
 *   APPROVAL  -> the same, plus an approval_rule in the contract so this agent
 *                needs a human for that action without imposing it on every
 *                other agent in the tenant
 *   DENY      -> nothing is written. Least privilege is deny-by-default, so a
 *                capability that is not granted is already refused.
 *
 * Nothing here is cosmetic: if the wizard is closed halfway the agent exists
 * with exactly the authority that was actually written, and no more.
 */
export default function NewAgent() {
  const nav = useNavigate();
  const [step, setStep] = useState(1);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [purpose, setPurpose] = useState("");
  const [catalogue, setCatalogue] = useState<Capability[]>([]);
  const [choice, setChoice] = useState<Record<string, Disposition>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<{
    agentId: string;
    token: string;
    expiresAt: string | null;
  } | null>(null);

  useEffect(() => {
    api
      .capabilities()
      .then((data) => {
        setCatalogue(data.capabilities);
        const initial: Record<string, Disposition> = {};
        data.capabilities.forEach((cap) => {
          initial[`${cap.resource_kind}.${cap.action}`] = "DENY";
        });
        setChoice(initial);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "failed"));
  }, []);

  const key = (cap: Capability) => `${cap.resource_kind}.${cap.action}`;
  const granted = catalogue.filter((cap) => choice[key(cap)] !== "DENY");

  const create = async () => {
    setBusy(true);
    setError("");
    try {
      const made = await api.createAgent({
        name,
        provider: "custom",
        model: "external",
        description,
      });
      const agentId = made.agent.id;

      for (const cap of granted) {
        await api.addPermission(agentId, {
          resource_kind: cap.resource_kind,
          action: cap.action,
          scope: cap.default_scope,
          effect: "allow",
        });
      }

      const byKind: Record<string, string[]> = {};
      granted.forEach((cap) => {
        byKind[cap.resource_kind] = byKind[cap.resource_kind] ?? [];
        byKind[cap.resource_kind].push(cap.action);
      });

      await api.createContract(agentId, {
        organization_id: "set-by-server",
        agent_id: "set-by-server",
        contract_id: `${name.toLowerCase().replace(/[^a-z0-9]+/g, "-") || "agent"}-contract`,
        version: 1,
        status: "ACTIVE",
        purpose: purpose || description || name,
        capabilities: Object.entries(byKind).map(([kind, actions]) => ({
          name: kind,
          resource_kind: kind,
          actions,
        })),
        resources: Object.keys(byKind).map((kind) => ({
          kind,
          scope: kind === "crm" ? "customers" : "*",
        })),
        constraints: { payload_size: { max_bytes: 4096 } },
        data_constraints: { denied_fields: ["ssn", "secret", "password"] },
        approval_rules: granted
          .filter((cap) => choice[key(cap)] === "APPROVAL")
          .map((cap) => ({
            resource_kind: cap.resource_kind,
            action: cap.action,
            require: "human",
          })),
      });

      setCreated({
        agentId,
        token: (made as any).token,
        expiresAt: (made as any).expires_at ?? null,
      });
      setStep(4);
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not create agent");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <h2 className="page-title">Add agent</h2>
      <p className="page-sub">
        An agent starts with no authority at all. What you grant here is written
        to its permissions and its runtime contract; anything you leave denied is
        refused by default.
      </p>
      {error && <p className="flash">{error}</p>}

      {/* ---------------------------------------------------------------- */}
      {step === 1 && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>1. Identity</h3>
          <div className="grid" style={{ gap: 10, maxWidth: 560 }}>
            <label className="field">
              Name
              <input
                id="agent-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Sales Assistant"
              />
            </label>
            <label className="field">
              What is it for?
              <input
                id="agent-purpose"
                value={purpose}
                onChange={(event) => setPurpose(event.target.value)}
                placeholder="Read customer records and correct mistakes"
              />
            </label>
            <label className="field">
              Description (optional)
              <input
                id="agent-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </label>
            <div className="row">
              <button
                className="btn"
                disabled={!name.trim()}
                onClick={() => setStep(2)}
              >
                Next: capabilities
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ---------------------------------------------------------------- */}
      {step === 2 && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>2. What may it do?</h3>
          <p className="page-sub" style={{ marginTop: 0 }}>
            Everything is denied until you say otherwise.
          </p>
          <table>
            <thead>
              <tr>
                <th>Capability</th>
                <th>Enforcement</th>
                <th>Allow</th>
                <th>Needs a human</th>
                <th>Deny</th>
              </tr>
            </thead>
            <tbody>
              {catalogue.map((cap) => {
                const id = key(cap);
                return (
                  <tr key={id}>
                    <td className="mono">
                      {cap.resource_kind}.{cap.action}
                      {cap.irreversible && (
                        <>
                          {" "}
                          <span className="badge badge-critical">irreversible</span>
                        </>
                      )}
                    </td>
                    <td>
                      {cap.enforceable ? (
                        <span className="badge badge-ALLOW">executes</span>
                      ) : (
                        <span className="badge badge-medium">decision only</span>
                      )}
                    </td>
                    {(["ALLOW", "APPROVAL", "DENY"] as Disposition[]).map((option) => (
                      <td key={option}>
                        <input
                          type="radio"
                          id={`${id}-${option}`}
                          name={id}
                          checked={choice[id] === option}
                          onChange={() => setChoice({ ...choice, [id]: option })}
                        />
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="page-sub">
            <strong>Decision only</strong> means Aegis will rule on the request
            but no protected tool is connected, so nothing executes either way.
          </p>
          <div className="row">
            <button className="btn secondary" onClick={() => setStep(1)}>
              Back
            </button>
            <button className="btn" onClick={() => setStep(3)}>
              Next: review
            </button>
          </div>
        </div>
      )}

      {/* ---------------------------------------------------------------- */}
      {step === 3 && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>3. Review</h3>
          <table>
            <thead>
              <tr>
                <th>Capability</th>
                <th>Decision</th>
              </tr>
            </thead>
            <tbody>
              {catalogue.map((cap) => {
                const disposition = choice[key(cap)];
                return (
                  <tr key={key(cap)}>
                    <td className="mono">
                      {cap.resource_kind}.{cap.action}
                    </td>
                    <td>
                      <span
                        className={
                          "badge badge-" +
                          (disposition === "ALLOW"
                            ? "ALLOW"
                            : disposition === "APPROVAL"
                              ? "APPROVAL"
                              : "BLOCK")
                        }
                      >
                        {disposition === "APPROVAL" ? "NEEDS A HUMAN" : disposition}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {granted.length === 0 && (
            <p className="flash">
              Nothing is granted. The agent will be created and every request it
              makes will be refused. That is valid, but probably not what you
              want.
            </p>
          )}
          <div className="row">
            <button className="btn secondary" onClick={() => setStep(2)}>
              Back
            </button>
            <button className="btn" disabled={busy} onClick={create}>
              {busy ? "Creating…" : "Create agent"}
            </button>
          </div>
        </div>
      )}

      {/* ---------------------------------------------------------------- */}
      {step === 4 && created && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>4. Credential</h3>
          <p className="page-sub" style={{ marginTop: 0 }}>
            This is the only time this token is shown. Aegis stores only its
            hash and cannot show it again — rotate the credential if you lose it.
          </p>
          <div className="token-box mono" id="agent-token">
            {created.token}
          </div>
          {created.expiresAt && (
            <p className="page-sub">
              Expires {new Date(created.expiresAt).toLocaleString()}.
            </p>
          )}
          <p className="page-sub">
            The agent presents this as <code>X-Agent-Token</code> to the
            enforcement gateway. It is not a credential for any protected system:
            those never leave the broker.
          </p>
          <div className="row">
            <button
              className="btn"
              onClick={() => nav(`/agents/${created.agentId}`)}
            >
              Open agent
            </button>
          </div>
        </div>
      )}
    </>
  );
}
