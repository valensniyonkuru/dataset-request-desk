import { type FormEvent, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { ErrorMessage, messageOf } from "../ui";

// Demo accounts from seed/users.json. The brief asks for them to be documented;
// they are created on every start of the API and are not for real use.
const DEMO_ACCOUNTS = [
  { email: "admin@example.com", password: "admin123", role: "admin" },
  { email: "ops1@example.com", password: "ops123", role: "operator" },
  { email: "ops2@example.com", password: "ops123", role: "operator" },
  { email: "client-a@example.com", password: "client123", role: "client" },
  { email: "client-b@example.com", password: "client123", role: "client" },
];

export default function LoginPage() {
  const { user, loading, notice, login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!loading && user !== null) {
    return <Navigate to="/requests" replace />;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate("/requests");
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <h1>Dataset Request Desk</h1>
      {notice && (
        <p role="status" className="message message-info">
          {notice}
        </p>
      )}
      <form onSubmit={submit} className="form">
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
          />
        </label>
        <ErrorMessage message={error} />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Logging in..." : "Log in"}
        </button>
      </form>

      <section className="hint" aria-labelledby="demo-accounts">
        <h2 id="demo-accounts">Demo accounts</h2>
        <p>Created from seed/users.json. Pick one to fill in the form.</p>
        <table>
          <thead>
            <tr>
              <th scope="col">Email</th>
              <th scope="col">Role</th>
              <th scope="col">Password</th>
              <th scope="col">
                <span className="visually-hidden">Action</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {DEMO_ACCOUNTS.map((account) => (
              <tr key={account.email}>
                <td>{account.email}</td>
                <td>{account.role}</td>
                <td>{account.password}</td>
                <td>
                  <button
                    type="button"
                    aria-label={`Use ${account.email}`}
                    onClick={() => {
                      setEmail(account.email);
                      setPassword(account.password);
                    }}
                  >
                    Use this account
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
