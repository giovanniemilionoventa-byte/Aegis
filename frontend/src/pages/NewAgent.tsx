import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Capability } from "../api";
import ConnectCard from "../components/ConnectCard";
import { capLabel, useT, type Key } from "../i18n";

type Disposition = "ALLOW" | "APPROVAL" | "DENY";
type Preset = "recommended" | "custom";

const DISPOSITIONS: Array<[Disposition, Key]> = [
  ["ALLOW", "new.allow"],
  ["APPROVAL", "new.human"],
  ["DENY", "new.deny"],
];

/**
 * Agent onboarding.
 *
 * "Recommended" is one request: the server creates the agent with a sensible
 * authority (read freely, send and change through a person, never delete, move
 * money or export). "Custom" is the per-action table, and every choice in it
 * becomes real backend state in places that already existed and are
 * authoritative:
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
  const t = useT();
  const nav = useNavigate();
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [preset, setPreset] = useState<Preset>("recommended");
  const [catalogue, setCatalogue] = useState<Capability[]>([]);
  const [gmailOffered, setGmailOffered] = useState(false);
  const [choice, setChoice] = useState<Record<string, Disposition>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<{ agentId: string; token: string } | null>(null);

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
      .catch((err) => setError(err instanceof Error ? err.message : t("common.failed")));
    api.health().then((health) => setGmailOffered(Boolean(health.features?.gmail))).catch(() => {});
  }, [t]);

  const key = (cap: Capability) => `${cap.resource_kind}.${cap.action}`;
  // Gmail is a pilot-only connector: a deployment that does not offer it does not show it.
  const visible = catalogue.filter((cap) => cap.resource_kind !== "gmail" || gmailOffered);
  const granted = visible.filter((cap) => choice[key(cap)] !== "DENY");

  const grantCustom = async (agentId: string) => {
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
      purpose: purpose || name,
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
  };

  const create = async () => {
    setBusy(true);
    setError("");
    try {
      const made = await api.createAgent({
        name,
        provider: "custom",
        model: "external",
        description: purpose,
        ...(preset === "recommended" ? { preset: "recommended" as const } : {}),
      });
      if (preset === "custom") await grantCustom(made.agent.id);
      setCreated({ agentId: made.agent.id, token: made.token });
      setStep(4);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setBusy(false);
    }
  };

  const dispositionBadge = (disposition: Disposition) =>
    disposition === "ALLOW" ? "ALLOW" : disposition === "APPROVAL" ? "APPROVAL" : "BLOCK";
  const dispositionLabel = (disposition: Disposition) =>
    t(DISPOSITIONS.find(([value]) => value === disposition)![1]);

  return (
    <>
      <h1 className="page-title">{t("new.title")}</h1>
      <p className="page-sub">{t("new.lead")}</p>
      {error && (
        <p className="flash" role="alert">
          {error}
        </p>
      )}

      {step === 1 && (
        <form
          className="card"
          onSubmit={(event) => {
            event.preventDefault();
            if (preset === "recommended") create();
            else setStep(2);
          }}
        >
          <div className="grid" style={{ gap: 16, maxWidth: 560 }}>
            <label className="field">
              {t("new.name")}
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("new.namePh")}
                autoComplete="off"
                required
              />
            </label>
            <label className="field">
              {t("new.purpose")}
              <input
                value={purpose}
                onChange={(event) => setPurpose(event.target.value)}
                placeholder={t("new.purposePh")}
                autoComplete="off"
              />
            </label>
            <fieldset className="grid" style={{ gap: 10 }}>
              <legend>{t("new.presetLegend")}</legend>
              {(
                [
                  ["recommended", "new.presetRecommended", "new.presetRecommendedDesc"],
                  ["custom", "new.presetCustom", "new.presetCustomDesc"],
                ] as Array<[Preset, Key, Key]>
              ).map(([value, title, description]) => (
                <label className="choice" key={value}>
                  <input
                    type="radio"
                    name="preset"
                    checked={preset === value}
                    onChange={() => setPreset(value)}
                  />
                  <div>
                    <strong>{t(title)}</strong>
                    <span>{t(description)}</span>
                  </div>
                </label>
              ))}
            </fieldset>
            <div className="row">
              <button className="btn large" type="submit" disabled={busy || !name.trim()}>
                {preset === "recommended"
                  ? busy
                    ? t("new.creating")
                    : t("new.create")
                  : t("new.toCaps")}
              </button>
            </div>
          </div>
        </form>
      )}

      {step === 2 && (
        <div className="card">
          <h2>{t("new.stepCaps")}</h2>
          <p className="page-sub">{t("new.capsLead")}</p>
          <table>
            <caption className="visually-hidden">{t("new.stepCaps")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("new.colCap")}</th>
                <th scope="col">{t("new.colMode")}</th>
                {DISPOSITIONS.map(([value, label]) => (
                  <th scope="col" key={value}>
                    {t(label)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map((cap) => {
                const id = key(cap);
                const label = capLabel(cap.resource_kind, cap.action);
                return (
                  <tr key={id}>
                    <th scope="row" style={{ fontWeight: 600 }}>
                      {label}
                      {cap.irreversible && (
                        <>
                          {" "}
                          <span className="badge badge-critical">{t("new.irreversible")}</span>
                        </>
                      )}
                      <div className="hint mono">{id}</div>
                    </th>
                    <td>
                      {cap.enforceable ? (
                        <span className="badge badge-ALLOW">{t("new.executes")}</span>
                      ) : (
                        <span className="badge badge-medium">{t("new.decisionOnly")}</span>
                      )}
                    </td>
                    {DISPOSITIONS.map(([option, optionLabel]) => (
                      <td key={option}>
                        <label className="cell-choice">
                          <input
                            type="radio"
                            name={id}
                            checked={choice[id] === option}
                            aria-label={`${label}: ${t(optionLabel)}`}
                            onChange={() => setChoice({ ...choice, [id]: option })}
                          />
                        </label>
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="hint">{t("new.decisionOnlyNote")}</p>
          <p className="hint">{t("policy.note")}</p>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn secondary" onClick={() => setStep(1)}>
              {t("common.back")}
            </button>
            <button className="btn" onClick={() => setStep(3)}>
              {t("new.toReview")}
            </button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="card">
          <h2>{t("new.review")}</h2>
          <table>
            <caption className="visually-hidden">{t("new.review")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("new.colCap")}</th>
                <th scope="col">{t("appr.colResult")}</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((cap) => {
                const disposition = choice[key(cap)] ?? "DENY";
                return (
                  <tr key={key(cap)}>
                    <th scope="row" style={{ fontWeight: 600 }}>
                      {capLabel(cap.resource_kind, cap.action)}
                    </th>
                    <td>
                      <span className={"badge badge-" + dispositionBadge(disposition)}>
                        {dispositionLabel(disposition)}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {granted.length === 0 && <p className="flash">{t("new.nothingGranted")}</p>}
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn secondary" onClick={() => setStep(2)}>
              {t("common.back")}
            </button>
            <button className="btn" disabled={busy} onClick={create}>
              {busy ? t("new.creating") : t("new.create")}
            </button>
          </div>
        </div>
      )}

      {step === 4 && created && (
        <>
          <h2>{t("new.stepConnect")}</h2>
          <ConnectCard agentId={created.agentId} token={created.token} />
          {preset === "custom" && granted.some((cap) => cap.resource_kind === "gmail") && (
            <p className="flash">{t("new.gmailNote")}</p>
          )}
          <div className="row" style={{ marginTop: 16 }}>
            <button className="btn" onClick={() => nav(`/agents/${created.agentId}`)}>
              {t("new.openAgent")}
            </button>
          </div>
        </>
      )}
    </>
  );
}
