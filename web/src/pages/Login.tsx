import { FormEvent, useState } from "react";
import { api, ApiError } from "../api";
import { S } from "../strings.pt";

export function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(username, password);
      onLoggedIn();
    } catch (err) {
      setError(err instanceof ApiError && err.detail ? err.detail : S.login.falha);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <form className="card" onSubmit={submit}>
        <h1>{S.login.titulo}</h1>
        <label>
          {S.login.usuario}
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label>
          {S.login.senha}
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error && <p role="alert" className="error">{error}</p>}
        <button type="submit" className="primary" disabled={busy}>{S.login.entrar}</button>
      </form>
    </main>
  );
}
