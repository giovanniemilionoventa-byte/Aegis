import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type ApprovalLink } from "../api";
import ApprovalPreview from "../components/ApprovalPreview";
import LangSwitch from "../components/LangSwitch";
import { capLabel, useLang, useT, type Key } from "../i18n";
import { timeAgo } from "../time";

type Screen =
  | { kind: "loading" }
  | { kind: "ready"; link: ApprovalLink }
  | { kind: "done"; approved: boolean }
  | { kind: "problem"; message: Key };

const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);

// A request that is no longer waiting cannot be decided; say why in plain words.
const fromLink = (link: ApprovalLink): Screen =>
  link.effective_status === "pending"
    ? { kind: "ready", link }
    : { kind: "problem", message: link.effective_status === "expired" ? "link.expired" : "link.already" };

const problem = (err: unknown): Screen => ({
  kind: "problem",
  message:
    err instanceof ApiError && err.status === 429
      ? "link.tooMany"
      : err instanceof ApiError && err.status === 404
        ? "link.invalid"
        : "common.failed",
});

/**
 * The page behind the link in the notification email. There is no login: the
 * signed link names one request and one reviewer and stops working with the
 * request. It is meant to be used from a phone, so the two answers are the only
 * things on it and both are large.
 */
export default function ApproveLink() {
  const t = useT();
  const lang = useLang();
  const { token = "" } = useParams();
  const [screen, setScreen] = useState<Screen>({ kind: "loading" });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    document.title = `${t("link.title")} · Aegis`;
  }, [lang, t]);

  useEffect(() => {
    let live = true;
    api
      .approvalLink(token)
      .then((link) => live && setScreen(fromLink(link)))
      .catch((err) => live && setScreen(problem(err)));
    return () => {
      live = false;
    };
  }, [token]);

  const decide = async (decision: "ALLOW" | "BLOCK") => {
    setBusy(true);
    try {
      await api.approvalLinkDecide(token, decision);
      setScreen({ kind: "done", approved: decision === "ALLOW" });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Someone else answered first, or it expired while this page was open.
        try {
          setScreen(fromLink(await api.approvalLink(token)));
        } catch (inner) {
          setScreen(problem(inner));
        }
      } else {
        setScreen(problem(err));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-wrap">
      <main className="card auth-card wide" aria-live="polite">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true" />
          <div>
            <strong>AEGIS</strong>
            <span>{t("link.title")}</span>
          </div>
        </div>

        {screen.kind === "loading" && <p>{t("common.loading")}</p>}

        {screen.kind === "ready" && (
          <>
            <h1 className="page-title">
              {t("appr.wants", {
                agent: screen.link.agent_name ?? "—",
                action: lowerFirst(capLabel(screen.link.resource_kind, screen.link.action)),
              })}
            </h1>
            <p className="hint">
              {t("appr.raised", { when: timeAgo(screen.link.created_at) })}
              {screen.link.expires_at &&
                ` · ${t("appr.expires", { when: timeAgo(screen.link.expires_at) })}`}
            </p>
            {screen.link.destination === "external" && (
              <p>
                <span className="badge badge-APPROVAL">{t("appr.external")}</span>
              </p>
            )}
            <ApprovalPreview preview={screen.link.preview} />
            <div className="actions">
              <button className="btn large" disabled={busy} onClick={() => decide("ALLOW")}>
                {t("appr.approve")}
              </button>
              <button className="btn large danger" disabled={busy} onClick={() => decide("BLOCK")}>
                {t("appr.deny")}
              </button>
            </div>
          </>
        )}

        {screen.kind === "done" && (
          <>
            <h1 className="page-title">
              {screen.approved ? t("link.doneApproved") : t("link.doneDenied")}
            </h1>
            <p className="flash ok">
              {screen.approved ? t("link.doneApprovedSub") : t("link.doneDeniedSub")}
            </p>
          </>
        )}

        {screen.kind === "problem" && (
          <>
            <h1 className="page-title">{t("link.title")}</h1>
            <p className="flash" role="alert">
              {t(screen.message)}
            </p>
          </>
        )}

        {screen.kind !== "loading" && screen.kind !== "ready" && (
          <p>
            <Link className="btn secondary" to="/approvals">
              {t("link.openApp")}
            </Link>
          </p>
        )}
        <LangSwitch />
      </main>
    </div>
  );
}
