import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, setToken } from "../api";
import LangSwitch from "../components/LangSwitch";
import { useLang, useT } from "../i18n";

// Documented in the README as the local demo account. It only exists when the
// server reports `features.demo` (never in production), and it is never
// pre-filled: signing in with it is an explicit click.
const DEMO = { email: "admin@acme.test", password: "aegis-demo" };

export default function Login() {
  const t = useT();
  const lang = useLang();
  const nav = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [org, setOrg] = useState("");
  const [invite, setInvite] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [demo, setDemo] = useState(false);

  useEffect(() => {
    api.health().then((health) => setDemo(Boolean(health.features?.demo))).catch(() => {});
  }, []);

  useEffect(() => {
    document.title = `${mode === "login" ? t("login.title") : t("login.registerTitle")} · Aegis`;
  }, [mode, lang, t]);

  const explain = (err: unknown) => {
    if (!(err instanceof ApiError)) return t("common.failed");
    if (err.status === 429) return t("login.tooMany");
    if (mode === "login" && err.status === 401) return t("login.badLogin");
    if (mode === "register" && err.status === 403) return t("login.badInvite");
    return err.message;
  };

  const enter = async (attempt: () => Promise<{ access_token: string }>) => {
    setError("");
    setBusy(true);
    try {
      setToken((await attempt()).access_token);
      nav("/");
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    enter(() =>
      mode === "login"
        ? api.login(email, password)
        : api.register({
            organization_name: org,
            full_name: fullName,
            email,
            password,
            invite_code: invite.trim() || undefined,
          }),
    );
  };

  const registering = mode === "register";

  return (
    <div className="auth-wrap">
      <main className="card auth-card">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true" />
          <div>
            <strong>AEGIS</strong>
            <span>{t("shell.tagline")}</span>
          </div>
        </div>
        <h1 className="page-title">{registering ? t("login.registerTitle") : t("login.title")}</h1>
        <p className="page-sub">{t("login.lead")}</p>

        <form onSubmit={submit} className="grid" style={{ gap: 14 }}>
          {registering && (
            <>
              <label className="field">
                {t("login.org")}
                <input
                  value={org}
                  onChange={(event) => setOrg(event.target.value)}
                  autoComplete="organization"
                  required
                />
              </label>
              <label className="field">
                {t("login.name")}
                <input
                  value={fullName}
                  onChange={(event) => setFullName(event.target.value)}
                  autoComplete="name"
                  required
                />
              </label>
            </>
          )}
          <label className="field">
            {t("login.email")}
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label className="field">
            {t("login.password")}
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={registering ? "new-password" : "current-password"}
              minLength={registering ? 12 : undefined}
              required
            />
            {registering && <span className="hint">{t("login.passwordHint")}</span>}
          </label>
          {registering && (
            <label className="field">
              {t("login.invite")}
              <input
                value={invite}
                onChange={(event) => setInvite(event.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
              <span className="hint">{t("login.inviteHint")}</span>
            </label>
          )}
          {error && (
            <p className="flash" role="alert">
              {error}
            </p>
          )}
          <button className="btn large" type="submit" disabled={busy}>
            {busy ? t("common.wait") : registering ? t("login.registerSubmit") : t("login.submit")}
          </button>
        </form>

        <div className="grid" style={{ gap: 10, marginTop: 16 }}>
          <button
            type="button"
            className="btn secondary"
            onClick={() => {
              setError("");
              setMode(registering ? "login" : "register");
            }}
          >
            {registering ? t("login.toLogin") : t("login.toRegister")}
          </button>
          {demo && !registering && (
            <button
              type="button"
              className="btn secondary"
              disabled={busy}
              onClick={() => enter(() => api.login(DEMO.email, DEMO.password))}
            >
              {t("login.demo")}
            </button>
          )}
          <LangSwitch />
        </div>
      </main>
    </div>
  );
}
