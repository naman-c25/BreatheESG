import { useState, FormEvent } from "react";
import { motion } from "framer-motion";
import { api } from "../api";

export function Login({ onAuthed }: { onAuthed: () => void }) {
  const [email, setEmail] = useState("analyst@acme.example");
  const [password, setPassword] = useState("demo1234");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.login(email, password);
      onAuthed();
    } catch (err: any) {
      setError(err.status === 401 ? "Invalid email or password" : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <motion.form
        className="login-card"
        onSubmit={submit}
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25, ease: "easeOut" }}
      >
        <h1>Breathe ESG</h1>
        <div className="sub">Sign in to the ingestion & review console.</div>

        <label htmlFor="email">Email</label>
        <input id="email" type="email" autoFocus value={email} onChange={e => setEmail(e.target.value)} />

        <label htmlFor="password">Password</label>
        <input id="password" type="password" value={password} onChange={e => setPassword(e.target.value)} />

        <div style={{ marginTop: 18 }}>
          <button className="btn primary" disabled={busy} style={{ width: "100%" }}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </div>

        {error && <div className="error">{error}</div>}

        <div className="demo-hint">
          <strong>Demo accounts:</strong><br />
          analyst@acme.example · demo1234 (US tenant)<br />
          analyst@globex.example · demo1234 (DE tenant)
        </div>
      </motion.form>
    </div>
  );
}
