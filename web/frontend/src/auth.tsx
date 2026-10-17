import { createContext, useContext, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { api, ApiError } from "./api";
import type { UserSummary } from "./types";

interface AuthContextValue {
  user: UserSummary | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserSummary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    api.session()
      .then((session) => { if (active) setUser(session.authenticated ? session.user : null); })
      .catch(() => { if (active) setUser(null); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    loading,
    login: async (username, password) => {
      const result = await api.login(username, password);
      if (!result.user) throw new ApiError(401, "로그인 정보를 확인해 주세요.");
      setUser(result.user);
    },
    logout: async () => {
      try { await api.logout(); } finally { setUser(null); }
    }
  }), [loading, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider가 필요합니다.");
  return value;
}

export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(username, password);
      setPassword("");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "로그인할 수 없습니다. 서버 상태를 확인해 주세요.");
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-card" aria-labelledby="login-title">
        <div className="brand-mark" aria-hidden="true">W</div>
        <p className="eyebrow">PRIVATE INVESTMENT WORKSPACE</p>
        <h1 id="login-title">Wealthy에 로그인</h1>
        <p className="muted">초기 관리자가 만든 계정만 사용할 수 있습니다. 공개 가입은 제공하지 않습니다.</p>
        <form onSubmit={submit} className="stack-lg">
          <label>사용자 이름<input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required maxLength={80} /></label>
          <label>비밀번호<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required maxLength={256} /></label>
          {error && <p className="message error" role="alert">{error}</p>}
          <button className="primary wide" type="submit" disabled={busy}>{busy ? "확인 중…" : "로그인"}</button>
        </form>
        <div className="security-note"><span aria-hidden="true">●</span> HTTPS 운영 환경에서만 인증정보를 전송하세요.</div>
      </section>
    </main>
  );
}
