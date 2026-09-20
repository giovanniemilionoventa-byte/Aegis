import { Navigate, NavLink, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { api, clearToken, getToken, type HealthReport, type Org, type User } from "./api";
import { useRouteA11y } from "./a11y";
import LangSwitch from "./components/LangSwitch";
import { useT, type Key } from "./i18n";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Agents from "./pages/Agents";
import Policies from "./pages/Policies";
import Events from "./pages/Events";
import Activity from "./pages/Activity";
import Posture from "./pages/Posture";
import Approvals from "./pages/Approvals";
import ApproveLink from "./pages/ApproveLink";
import Playground from "./pages/Playground";
import Contracts from "./pages/Contracts";
import NewAgent from "./pages/NewAgent";
import AgentDetail from "./pages/AgentDetail";
import Evidence from "./pages/Evidence";
import Gmail from "./pages/Gmail";

// What a customer uses every day. Everything else lives under "Advanced".
const PRIMARY: Array<[string, Key]> = [
  ["/", "nav.home"],
  ["/agents", "nav.agents"],
  ["/approvals", "nav.approvals"],
  ["/policies", "nav.rules"],
  ["/activity", "nav.activity"],
];
const ADVANCED: Array<[string, Key]> = [
  ["/contracts", "nav.contracts"],
  ["/gmail", "nav.gmail"],
  ["/posture", "nav.security"],
  ["/events", "nav.audit"],
  ["/evidence", "nav.evidence"],
  ["/playground", "nav.try"],
];

function Shell() {
  const t = useT();
  const nav = useNavigate();
  const { pathname } = useLocation();
  const [me, setMe] = useState<{ user: User; organization: Org } | null>(null);
  const [features, setFeatures] = useState<NonNullable<HealthReport["features"]>>({});
  const [pending, setPending] = useState(0);
  useRouteA11y();

  useEffect(() => {
    api.me().then(setMe).catch(() => nav("/login"));
    api.health().then((health) => setFeatures(health.features ?? {})).catch(() => {});
  }, [nav]);

  // The badge on "Approvals" is the one thing a person must not miss, so it is
  // refreshed on its own and right after a decision on that page.
  useEffect(() => {
    let live = true;
    const load = () => {
      api
        .stats()
        .then((stats) => live && setPending(stats.pending_approvals))
        .catch(() => {});
    };
    load();
    const timer = window.setInterval(() => {
      if (!document.hidden) load();
    }, 10000);
    window.addEventListener("aegis:approvals", load);
    return () => {
      live = false;
      window.clearInterval(timer);
      window.removeEventListener("aegis:approvals", load);
    };
  }, []);

  const advanced = ADVANCED.filter(([to]) => to !== "/gmail" || features.gmail);
  const advancedActive = advanced.some(([to]) => pathname.startsWith(to));

  const link = ([to, key]: [string, Key]) => (
    <NavLink
      key={to}
      to={to}
      end={to === "/"}
      className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}
    >
      <span>{t(key)}</span>
      {to === "/approvals" && pending > 0 && (
        <span className="nav-count">
          <span aria-hidden="true">{pending}</span>
          <span className="visually-hidden">{t("shell.pending", { n: pending })}</span>
        </span>
      )}
    </NavLink>
  );

  return (
    <div className="layout">
      <a className="skip-link" href="#main">
        {t("shell.skip")}
      </a>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true" />
          <div>
            <strong>AEGIS</strong>
            <span>{t("shell.tagline")}</span>
          </div>
        </div>
        <nav aria-label={t("shell.menu")}>
          {PRIMARY.map(link)}
          <details className="nav-advanced" open={advancedActive}>
            <summary>{t("nav.advanced")}</summary>
            {advanced.map(link)}
          </details>
        </nav>
        <div className="sidebar-foot">
          <div>
            {me?.organization.name}
            <br />
            {me?.user.email}
          </div>
          <LangSwitch />
          <button
            className="btn secondary small"
            onClick={() => {
              clearToken();
              nav("/login");
            }}
          >
            {t("shell.signOut")}
          </button>
        </div>
      </aside>
      <main id="main" className="main">
        <Outlet />
      </main>
    </div>
  );
}

function Private() {
  if (!getToken()) return <Navigate to="/login" replace />;
  return <Shell />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      {/* The link in the notification email: no session, the link is the credential. */}
      <Route path="/a/:token" element={<ApproveLink />} />
      <Route element={<Private />}>
        <Route path="/" element={<Overview />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/agents/new" element={<NewAgent />} />
        <Route path="/agents/:agentId" element={<AgentDetail />} />
        <Route path="/contracts" element={<Contracts />} />
        <Route path="/gmail" element={<Gmail />} />
        <Route path="/policies" element={<Policies />} />
        <Route path="/events" element={<Events />} />
        <Route path="/activity" element={<Activity />} />
        <Route path="/posture" element={<Posture />} />
        <Route path="/evidence" element={<Evidence />} />
        <Route path="/approvals" element={<Approvals />} />
        <Route path="/playground" element={<Playground />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
