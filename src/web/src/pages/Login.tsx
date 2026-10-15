import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";
import { ApiError } from "../api/client.ts";
import { useAuth } from "../auth/context.ts";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "../components/ui/Button.tsx";
import { Card } from "../components/ui/Card.tsx";
import { Field } from "../components/ui/Field.tsx";
import "../components/ui/ui.css";
import "../styles/surfaces/shell.css";

export function Login() {
  const { me, loading, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!loading && me) return <Navigate to={from} replace />;

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (e) {
      // A 401 is either wrong credentials or the login throttle ("Too many attempts"); the
      // server's message says which, so show it rather than one fixed text.
      setError(
        e instanceof ApiError && e.status === 401
          ? e.code === "unauthorized" && e.message
            ? e.message
            : "Email or password is not right."
          : e instanceof Error
            ? e.message
            : "Could not log in.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="shell-ground">
      <main className="public">
        <h1>Criba</h1>
        <p className="muted">Log in to work Applications.</p>
        <Card>
          <form className="stack" onSubmit={onSubmit}>
            <Field label="Email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
            <Field
              label="Password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {error && (
              <p className="notice error" role="alert">
                {error}
              </p>
            )}
            <Button variant="primary" type="submit" disabled={busy}>
              Log in
            </Button>
          </form>
        </Card>
      </main>
    </div>
  );
}
