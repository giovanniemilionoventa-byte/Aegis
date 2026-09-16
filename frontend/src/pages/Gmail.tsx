import { useCallback, useEffect, useState } from "react";
import { api, type GmailStatus } from "../api";

/**
 * Connecting a real mailbox, and showing an operator what that did and did not
 * give away.
 *
 * The page is deliberately small. Phase 19 needs exactly this: is a mailbox
 * connected, which one, what was granted, and a way to disconnect. Everything
 * else about Gmail -- decisions, approvals, evidence -- already has a page, and
 * duplicating it here would create a second version of the truth.
 *
 * TWO THINGS THIS PAGE WILL NOT DO.
 *
 * It will not display a credential, because no endpoint returns one. There is
 * no field in GmailStatus that could hold a token; that is a property of the
 * API, not a filter applied here.
 *
 * It will not claim the grant was revoked at Google when Aegis merely deleted
 * its copy. Disconnecting forgets the credential locally; the operator revokes
 * the grant itself at myaccount.google.com, and the page says so rather than
 * letting them believe otherwise.
 */
export default function Gmail() {
  const [status, setStatus] = useState<GmailStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const load = useCallback(() => {
    api
      .gmailStatus()
      .then((next) => {
        setStatus(next);
        setError("");
      })
      .catch((err) => setError(err instanceof Error ? err.message : "failed"));
  }, []);

  useEffect(load, [load]);

  const connect = async () => {
    setBusy(true);
    setNote("");
    try {
      const started = await api.gmailConnect();
      // Google's consent screen is entered by the operator, in their own
      // browser session. Aegis never handles the password and never sees it.
      window.location.href = started.authorization_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    try {
      const result = await api.gmailDisconnect();
      setNote(result.note);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
    } finally {
      setBusy(false);
    }
  };

  const unconfigured =
    status && (!status.oauth_client_configured || !status.encryption_key_configured);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h2>Gmail</h2>
          <p className="muted">
            A real mailbox behind the control layer. Agents never receive its
            credential.
          </p>
        </div>
        {status?.connected ? (
          <button className="btn secondary" disabled={busy} onClick={disconnect}>
            Disconnect
          </button>
        ) : (
          <button
            className="btn"
            disabled={busy || !!unconfigured}
            onClick={connect}
          >
            Connect Gmail
          </button>
        )}
      </header>

      {error && <div className="alert error">{error}</div>}
      {note && <div className="alert">{note}</div>}

      {unconfigured && (
        <div className="card">
          <h3>Not configured yet</h3>
          <p className="muted">
            This step needs a person. Aegis cannot create a Google OAuth client
            for you, and inventing one would leave a stack that looks configured
            and is not.
          </p>
          <ul>
            {!status?.oauth_client_configured && (
              <li>
                No Google OAuth client. Create one in Google Cloud Console and
                put the id and secret in <code>.env</code> as{" "}
                <code>AEGIS_GOOGLE_CLIENT_ID</code> and{" "}
                <code>AEGIS_GOOGLE_CLIENT_SECRET</code>. See{" "}
                <code>docs/PHASE_19_GMAIL.md</code> for the exact steps.
              </li>
            )}
            {!status?.encryption_key_configured && (
              <li>
                No <code>AEGIS_OAUTH_ENCRYPTION_KEY</code>. Generate one with{" "}
                <code>scripts/init-env.sh</code>. Without it Aegis refuses to
                store a refresh token rather than storing it in the clear.
              </li>
            )}
          </ul>
          <p className="muted">
            Redirect URI this deployment expects:{" "}
            <code>{status?.redirect_uri}</code>
          </p>
        </div>
      )}

      {status && !unconfigured && (
        <div className="card">
          <h3>Connection</h3>
          {status.connected && status.connection ? (
            <table className="table">
              <tbody>
                <tr>
                  <th>Mailbox</th>
                  <td>{status.connection.google_email || "(address unavailable)"}</td>
                </tr>
                <tr>
                  <th>Connected</th>
                  <td>{status.connection.connected_at}</td>
                </tr>
                <tr>
                  <th>Granted scopes</th>
                  <td>
                    {status.connection.scopes.map((scope) => (
                      <div key={scope}>
                        <code>{scope}</code>
                      </div>
                    ))}
                  </td>
                </tr>
                <tr>
                  <th>Credential</th>
                  <td>
                    Held server-side, sealed at rest. Not shown here, and not
                    returned by any API.
                  </td>
                </tr>
              </tbody>
            </table>
          ) : (
            <p className="muted">
              No mailbox connected. Agents with Gmail permissions will be
              refused until one is.
            </p>
          )}
        </div>
      )}

      <div className="card">
        <h3>What an agent may do with this mailbox</h3>
        <p className="muted">
          Decided by policy and the agent's runtime contract, not by this page.
          The rows below are the canonical operations; the decision for a given
          agent is on its own page.
        </p>
        <table className="table">
          <thead>
            <tr>
              <th>Operation</th>
              <th>Default posture</th>
              <th>Effect</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><code>gmail.search</code></td>
              <td>Allowed</td>
              <td>Read-only.</td>
            </tr>
            <tr>
              <td><code>gmail.read</code></td>
              <td>Allowed</td>
              <td>Read-only. Message content is data, never instructions.</td>
            </tr>
            <tr>
              <td><code>gmail.draft</code></td>
              <td>Allowed</td>
              <td>Nothing leaves the mailbox.</td>
            </tr>
            <tr>
              <td><code>gmail.send</code></td>
              <td>Approval required</td>
              <td>Irreversible. A person approves each one, once.</td>
            </tr>
            <tr>
              <td><code>gmail.delete</code></td>
              <td>Denied</td>
              <td>
                Refused by permissions, by policy and by contract. The scope
                Aegis requests from Google does not grant permanent deletion at
                all.
              </td>
            </tr>
          </tbody>
        </table>
        <p className="muted">
          Requested scope: <code>{status?.requested_scopes.join(", ")}</code>
        </p>
      </div>
    </div>
  );
}
