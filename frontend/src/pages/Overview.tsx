import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AlertRow, type EventRow, type Stats } from "../api";
import { decisionLabel, tOr, useT, type Key } from "../i18n";
import { timeAgo } from "../time";

/**
 * The first page: what needs the person right now, or, on an empty account,
 * what to do first. The numbers come from the same endpoints as before; only
 * the words and the order changed.
 */
export default function Overview() {
  const t = useT();
  const [stats, setStats] = useState<Stats | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.stats(), api.events(), api.alerts()])
      .then(([s, e, a]) => {
        setStats(s);
        setEvents(e.slice(0, 8));
        setAlerts(a.slice(0, 6));
      })
      .catch((err) => setError(err instanceof Error ? err.message : t("common.failed")));
  }, [t]);

  const waiting = stats?.pending_approvals ?? 0;
  const noAgents = stats !== null && stats.agents === 0;
  const tiles: Array<[Key, number | undefined]> = [
    ["home.statAgents", stats?.agents],
    ["home.statChecked", stats?.events],
    ["home.statBlocked", stats?.blocked],
    ["home.statWaiting", stats?.pending_approvals],
  ];

  return (
    <>
      <h1 className="page-title">{t("home.title")}</h1>
      <p className="page-sub">{t("home.lead")}</p>
      {error && (
        <p className="flash" role="alert">
          {error}
        </p>
      )}

      {waiting > 0 && (
        <section className="callout">
          <div>
            <h2>{waiting === 1 ? t("home.waitingOne") : t("home.waitingMany", { n: waiting })}</h2>
            <p>{t("home.waitingSub")}</p>
          </div>
          <Link className="btn large" to="/approvals">
            {t("home.review")}
          </Link>
        </section>
      )}

      {noAgents && (
        <section className="card" style={{ marginBottom: 20 }}>
          <h2>{t("home.startTitle")}</h2>
          <p className="page-sub" style={{ marginBottom: 0 }}>
            {t("home.startLead")}
          </p>
          <ol className="steps">
            <li>{t("home.step1")}</li>
            <li>{t("home.step2")}</li>
            <li>{t("home.step3")}</li>
          </ol>
          <Link className="btn large" to="/agents/new">
            {t("home.startCta")}
          </Link>
        </section>
      )}

      <div className="grid grid-4" style={{ marginBottom: 20 }}>
        {tiles.map(([label, value]) => (
          <div className="card" key={label}>
            <div className="stat-label">{t(label)}</div>
            <div className="stat-value">{value ?? "—"}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-2">
        <section className="card">
          <h2>{t("home.recent")}</h2>
          {events.length === 0 && (
            <div className="empty">{noAgents ? t("home.step3") : t("home.noEvents")}</div>
          )}
          {events.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>{t("home.colDecision")}</th>
                  <th>{t("home.colAction")}</th>
                  <th>{t("home.colRisk")}</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <tr key={event.id}>
                    <td>
                      <span className={"badge badge-" + event.decision}>
                        {decisionLabel(event.decision)}
                      </span>
                    </td>
                    <td>
                      <span className="mono">
                        {event.resource_kind}.{event.action} {event.scope}
                      </span>
                      <div className="hint">{timeAgo(event.created_at)}</div>
                    </td>
                    <td>
                      <span className={"badge badge-" + event.risk_level}>
                        {tOr(`risk.${event.risk_level}`, event.risk_level)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        <section className="card">
          <h2>{t("home.alerts")}</h2>
          {alerts.length === 0 && <div className="empty">{t("home.noAlerts")}</div>}
          {alerts.map((alert) => (
            <div key={alert.id} style={{ marginBottom: 12 }}>
              <span className={"badge badge-" + alert.severity}>{alert.severity}</span>{" "}
              <strong>{alert.title}</strong>
              <div className="page-sub" style={{ marginBottom: 0 }}>
                {alert.message}
              </div>
            </div>
          ))}
        </section>
      </div>
      <p className="hint" style={{ marginTop: 20 }}>
        {t("home.how")}
      </p>
    </>
  );
}
