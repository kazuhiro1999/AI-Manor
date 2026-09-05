import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { ModuleDefinition } from "../../app/module";
import { api, ApiError } from "../../app/api";
import { APP_NAME } from "../../app/brand";
import { useT } from "../../app/i18n";

function LoginScreen() {
  const t = useT();
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const submit = async () => {
    if (!passcode.trim()) {
      setError(t("login.passcodeRequired"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/auth/login", { method: "POST", body: { passcode } });
      navigate("/tasks");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("login.failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-shell">
      <div className="login-card">
        <h1>{t("login.heading", { app: APP_NAME })}</h1>
        {/* ADR-010 D7: login は自前のシェルを持つので ScreenHeader は使わず、その場に一行を置く。 */}
        <p className="panel-note">{t("login.hint", { app: APP_NAME })}</p>
        <div className="form-grid">
          <div className="form-row">
            <label htmlFor="login-passcode">{t("login.passcodeLabel")}</label>
            <input
              id="login-passcode"
              className="form-input"
              type="password"
              value={passcode}
              onChange={(e) => setPasscode(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
              }}
            />
          </div>
          {error && <div className="form-error">{error}</div>}
          <div className="form-actions">
            <button className="btn btn-primary" type="button" disabled={busy} onClick={submit}>
              {t("login.submit")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export const loginModule: ModuleDefinition = {
  id: "login",
  title: "nav.login",
  description: "login.description",
  icon: "🔑",
  order: 99,
  hideFromNav: true,
  routes: [{ index: true, element: <LoginScreen /> }],
};
