import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type ApprovalRow } from "../api";
import ApprovalPreview from "../components/ApprovalPreview";
import { capLabel, statusLabel, useT } from "../i18n";
import { formatDateTime, minutesSince, timeAgo } from "../time";

// The Python/n8n snippet waits about five minutes for a person. A request older
// than this is likely one the agent has already given up on.
const STALE_MINUTES = 6;

const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);
const effective = (row: ApprovalRow) => row.effective_status ?? row.status;

/**
 * Human approval.
 *
 * The person is shown what will happen (recipients, subject, the start of the
 * text) and answers with one of two large buttons. Approving lets the agent's
 * next attempt at that exact request run, once: the grant is bound to the
 * request, its parameters and its contract version, and cannot be reused.
 */
export default function Approvals() {
  const t = useT();
  const [rows, setRows] = useState<ApprovalRow[] | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setRows(await api.approvals());
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.failed"));
    }
  }, [t]);

  useEffect(() => {
    load();
    const timer = window.setInterval(() => {
      if (!document.hidden) load();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  const decide = async (row: ApprovalRow, decision: "ALLOW" | "BLOCK") => {
    setBusyId(row.id);
    setError("");
    setNotice("");
    try {
      await api.decide(row.id, decision);
      setNotice(decision === "ALLOW" ? t("appr.doneApproved") : t("appr.doneDenied"));
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? t("appr.conflict")
          : err instanceof Error
            ? err.message
            : t("common.failed"),
      );
    } finally {
      setBusyId(null);
      window.dispatchEvent(new Event("aegis:approvals")); // refresh the badge in the menu
      await load();
    }
  };

  const pending = (rows ?? []).filter((row) => effective(row) === "pending");
  const decided = (rows ?? []).filter((row) => effective(row) !== "pending");

  return (
    <>
      <h1 className="page-title">{t("appr.title")}</h1>
      <p className="page-sub">{t("appr.lead")}</p>
      {notice && (
        <p className="flash ok" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="flash" role="alert">
          {error}
        </p>
      )}

      <h2>{t("appr.waiting", { n: pending.length })}</h2>
      {rows === null && !error && <div className="empty">{t("common.loading")}</div>}
      {rows !== null && pending.length === 0 && (
        <div className="card">
          <div className="empty">{t("appr.none")}</div>
        </div>
      )}
      {pending.map((row) => {
        const headingId = `approval-${row.id}`;
        return (
          <article className="card approval-card" key={row.id} aria-labelledby={headingId}>
            <h3 id={headingId}>
              {t("appr.wants", {
                agent: row.agent_name ?? row.agent_id.slice(0, 8),
                action: lowerFirst(capLabel(row.resource_kind, row.action)),
              })}
            </h3>
            <p className="hint">
              {t("appr.raised", { when: timeAgo(row.created_at) })}
              {row.expires_at && ` · ${t("appr.expires", { when: timeAgo(row.expires_at) })}`}
            </p>
            {row.destination === "external" && (
              <p>
                <span className="badge badge-APPROVAL">{t("appr.external")}</span>
              </p>
            )}
            {minutesSince(row.created_at) >= STALE_MINUTES && (
              <p className="hint">{t("appr.old")}</p>
            )}

            <ApprovalPreview preview={row.preview} />
            {row.reason && (
              <p className="hint">
                {t("appr.reason")}: {row.reason}
              </p>
            )}

            <div className="actions">
              <button
                className="btn large"
                disabled={busyId === row.id}
                aria-describedby={headingId}
                onClick={() => decide(row, "ALLOW")}
              >
                {t("appr.approve")}
              </button>
              <button
                className="btn large danger"
                disabled={busyId === row.id}
                aria-describedby={headingId}
                onClick={() => decide(row, "BLOCK")}
              >
                {t("appr.deny")}
              </button>
            </div>

            <details className="snippet">
              <summary>{t("appr.details")}</summary>
              <table>
                <tbody>
                  <tr>
                    <th scope="row">{t("appr.execution")}</th>
                    <td className="mono">{row.execution_id ?? "—"}</td>
                  </tr>
                  <tr>
                    <th scope="row">{t("appr.request")}</th>
                    <td className="mono">{row.request_id ?? "—"}</td>
                  </tr>
                  <tr>
                    <th scope="row">{t("appr.contract")}</th>
                    <td className="mono">
                      {row.contract_id ?? "—"}
                      {row.contract_version ? ` v${row.contract_version}` : ""}
                    </td>
                  </tr>
                  <tr>
                    <th scope="row">{t("appr.digest")}</th>
                    <td className="mono">{row.param_hash ?? "—"}</td>
                  </tr>
                </tbody>
              </table>
              <p className="hint">{t("appr.digestNote")}</p>
            </details>
          </article>
        );
      })}

      <h2 style={{ marginTop: 28 }}>{t("appr.decided")}</h2>
      <div className="card">
        {rows !== null && decided.length === 0 && (
          <div className="empty">{t("appr.noneDecided")}</div>
        )}
        {decided.length > 0 && (
          <table>
            <caption className="visually-hidden">{t("appr.decided")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("appr.colAgent")}</th>
                <th scope="col">{t("appr.colAction")}</th>
                <th scope="col">{t("appr.colResult")}</th>
                <th scope="col">{t("appr.colWhen")}</th>
              </tr>
            </thead>
            <tbody>
              {decided.map((row) => {
                const status = effective(row);
                return (
                  <tr key={row.id}>
                    <td>{row.agent_name ?? row.agent_id.slice(0, 8)}</td>
                    <td>{capLabel(row.resource_kind, row.action)}</td>
                    <td>
                      <span className={"badge badge-" + status}>{statusLabel(status)}</span>
                      {status === "consumed" && <div className="hint">{t("appr.usedOnce")}</div>}
                      {status === "approved" && <div className="hint">{t("appr.notUsed")}</div>}
                    </td>
                    <td>{formatDateTime(row.reviewed_at ?? row.expires_at)}</td>
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
