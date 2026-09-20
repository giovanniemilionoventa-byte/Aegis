import { useEffect, useState } from "react";
import { api, type AgentSetup } from "../api";
import { useT } from "../i18n";
import { timeAgo } from "../time";

const KEY_PLACEHOLDER = "YOUR_AGENT_TOKEN";
const HOST_PLACEHOLDER = "https://YOUR-GATEWAY-HOST";

function useCopy(value: string) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked: the text is selectable, so it can still be copied by hand */
    }
  };
  return { copied, copy };
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const t = useT();
  const { copied, copy } = useCopy(value);
  return (
    <>
      <button
        type="button"
        className="btn secondary"
        aria-label={`${t("common.copy")}: ${label}`}
        onClick={copy}
      >
        {copied ? t("common.copied") : t("common.copy")}
      </button>
      <span className="visually-hidden" role="status">
        {copied ? t("common.copied") : ""}
      </span>
    </>
  );
}

function CopyRow({ value, label }: { value: string; label: string }) {
  return (
    <div className="copy-row">
      <code className="value">{value}</code>
      <CopyButton value={value} label={label} />
    </div>
  );
}

function CopyBlock({ value, label }: { value: string; label: string }) {
  return (
    <>
      <pre className="code" tabIndex={0} aria-label={label}>
        {value}
      </pre>
      <CopyButton value={value} label={label} />
    </>
  );
}

/**
 * How to point a real agent at Aegis: an address, a key, and a command that
 * works as pasted. Then the card waits with the person for the agent's first
 * call, so "did it work?" has an answer on the same screen.
 *
 * The key is only ever the `token` the parent still holds from the moment it
 * was created or rotated. The setup endpoint never returns one (Aegis stores a
 * hash and cannot show it again), so without `token` the snippets keep their
 * placeholder and the card says the key is gone.
 */
export default function ConnectCard({
  agentId,
  token,
  onRotate,
  rotating,
}: {
  agentId: string;
  token?: string | null;
  onRotate?: () => void;
  rotating?: boolean;
}) {
  const t = useT();
  const [setup, setSetup] = useState<AgentSetup | null>(null);
  const [lastSeen, setLastSeen] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .agentSetup(agentId)
      .then(setSetup)
      .catch((err) => setError(err instanceof Error ? err.message : t("common.failed")));
  }, [agentId, t]);

  // Quick while waiting for the first call, slow afterwards just to keep the time fresh.
  const seen = lastSeen !== null;
  useEffect(() => {
    let live = true;
    const load = () => {
      api
        .agent(agentId)
        .then((agent) => live && setLastSeen(agent.last_seen_at))
        .catch(() => {});
    };
    load();
    const timer = window.setInterval(
      () => {
        if (!document.hidden) load();
      },
      seen ? 30000 : 4000,
    );
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [agentId, seen]);

  if (error) {
    return (
      <section className="card" style={{ marginTop: 16 }}>
        <p className="flash" role="alert">
          {error}
        </p>
      </section>
    );
  }
  if (!setup) return null;

  const origin = window.location.origin;
  const publicAddress = Boolean(setup.authorize_url);
  const address = setup.authorize_url ?? `${origin}/api/authorize`;
  const fill = (text: string) =>
    text
      .split(HOST_PLACEHOLDER)
      .join(setup.gateway_url ?? origin)
      .split(KEY_PLACEHOLDER)
      .join(token ?? KEY_PLACEHOLDER);

  return (
    <section className="card" style={{ marginTop: 16 }}>
      <h2>{t("cc.title")}</h2>
      <p className="page-sub">{t("cc.lead")}</p>

      <p className={"status " + (seen ? "ok" : "wait")} role="status">
        <span className="dot" aria-hidden="true" />
        {seen ? t("cc.seen", { when: timeAgo(lastSeen) }) : t("cc.waiting")}
      </p>

      <h3>{t("cc.address")}</h3>
      <CopyRow value={address} label={t("cc.address")} />
      {!publicAddress && <p className="hint">{t("cc.addressLocal")}</p>}

      <h3 style={{ marginTop: 20 }}>{t("cc.key")}</h3>
      {token ? (
        <>
          <CopyRow value={token} label={t("cc.key")} />
          <p className="hint">{t("cc.keyOnce")}</p>
        </>
      ) : (
        <>
          <p className="hint">{t("cc.keyLost")}</p>
          {onRotate && (
            <button
              type="button"
              className="btn secondary small"
              disabled={rotating}
              onClick={onRotate}
            >
              {t("cc.rotate")}
            </button>
          )}
        </>
      )}

      <h3 style={{ marginTop: 20 }}>{t("cc.tool")}</h3>
      <details className="snippet" open>
        <summary>{t("cc.n8nTitle")}</summary>
        <p>{t("cc.n8nHow")}</p>
        <CopyBlock value={fill(setup.snippets.curl)} label={t("cc.n8nTitle")} />
      </details>
      <details className="snippet">
        <summary>{t("cc.pythonTitle")}</summary>
        <CopyBlock value={fill(setup.snippets.python)} label={t("cc.pythonTitle")} />
      </details>
      <p className="hint">{token ? t("cc.keyIn") : t("cc.keyOut")}</p>
    </section>
  );
}
