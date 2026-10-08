import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { LanguageSwitch } from "../components/shell/AppShell";
import { Hub } from "../components/ui/Hub";
import { Icon, type IconName } from "../components/ui/Icon";
import { Logo } from "../components/ui/Logo";
import { useI18n, type TKey } from "../i18n";
import { ApiError } from "../services/api";
import { useAuth } from "../state/auth";

type Mode = "signin" | "create";

/** Position of each pill around the 460 px hub, copied from screens/signin.html. */
const PILLS: Array<{ label: TKey; icon: IconName; style: CSSProperties; gold?: boolean }> = [
  { label: "signin.pill.internal", icon: "building", style: { left: 230, top: 39.5, transform: "translate(-50%,-100%)" } },
  { label: "signin.pill.competitors", icon: "target", style: { left: 420.5, top: 215 } },
  { label: "signin.pill.market", icon: "trend", style: { left: 230, top: 420.5, transform: "translateX(-50%)" } },
  { label: "signin.pill.customers", icon: "users", style: { left: 39.5, top: 215, transform: "translateX(-100%)" } },
  { label: "signin.pill.verification", icon: "shield", style: { left: 366.83, top: 96.17 } },
  { label: "signin.pill.synthesis", icon: "layers", style: { left: 366.83, top: 333.83 } },
  { label: "signin.pill.approval", icon: "checks", style: { left: 93.17, top: 333.83, transform: "translateX(-100%)" }, gold: true },
  { label: "signin.pill.sources", icon: "link", style: { left: 93.17, top: 96.17, transform: "translateX(-100%)" } },
];

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD = 8;

/**
 * The hub is drawn for a 900 px tall panel. On a shorter window it shrinks so it does
 * not run into the headline.
 */
function useHubScale(panel: React.RefObject<HTMLElement | null>): number {
  const [scale, setScale] = useState(1);
  useLayoutEffect(() => {
    const element = panel.current;
    if (!element) return;
    const update = () => {
      const free = element.clientHeight - 96 - 308;
      setScale(Math.max(0.6, Math.min(1, free / 460)));
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [panel]);
  return scale;
}

export function SignIn() {
  const { t } = useI18n();
  const { session, signIn, createAccount } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<TKey | null>(null);
  const brandRef = useRef<HTMLElement>(null);
  const scale = useHubScale(brandRef);
  // Set once the form is submitted, so signing in does not trigger the "already signed in" redirect
  // below and race the navigation the submit chooses (Company setup after creating an account).
  const submitted = useRef(false);

  useEffect(() => {
    document.title = `${t(mode === "signin" ? "signin.eyebrow.signin" : "signin.eyebrow.create")} · Edrak`;
  }, [mode, t]);

  if (session && !submitted.current) return <Navigate to="/" replace />;

  const emailError: TKey | null = !email.trim() ? "signin.error.emailRequired" : !EMAIL_PATTERN.test(email.trim()) ? "signin.error.emailInvalid" : null;
  const passwordError: TKey | null = !password
    ? "signin.error.passwordRequired"
    : mode === "create" && password.length < MIN_PASSWORD
      ? "signin.error.passwordShort"
      : null;

  const switchMode = (next: Mode) => {
    setMode(next);
    setTouched(false);
    setFormError(null);
  };

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    setFormError(null);
    if (emailError || passwordError) return;
    setBusy(true);
    submitted.current = true;
    try {
      if (mode === "signin") {
        await signIn(email, password);
        const from = (location.state as { from?: string } | null)?.from;
        navigate(from && from !== "/sign-in" ? from : "/", { replace: true });
      } else {
        await createAccount(email, password);
        navigate("/company", { replace: true });
      }
    } catch (error) {
      if (error instanceof ApiError && error.code === "email_taken") setFormError("signin.error.emailTaken");
      else if (error instanceof ApiError && error.code === "invalid_credentials") setFormError("signin.error.credentials");
      else setFormError("common.error.generic");
      submitted.current = false;
      setBusy(false);
    }
  };

  const creating = mode === "create";

  return (
    <div className="signin">
      <section className="signin-brand" ref={brandRef} aria-label="Edrak">
        <Logo variant="reversed" height={30} style={{ alignSelf: "flex-start" }} />

        <div className="hub-box" role="img" aria-label={t("signin.hubLabel")} style={{ transform: `scale(${scale})` }}>
          <div style={{ position: "absolute", inset: 0 }}>
            <Hub size={460} color="#5B84FF" line={0.7} node={3.6} center={1.9} />
          </div>
          {PILLS.map((pill) => (
            <div key={pill.label} className="hub-pill" style={{ ...pill.style, color: pill.gold ? "#FFE66B" : "#F4F3EE" }}>
              <Icon name={pill.icon} size={15} />
              {t(pill.label)}
            </div>
          ))}
        </div>

        <div className="rel" style={{ maxWidth: 520 }}>
          <div className="mono" style={{ color: "#8D8FC0", marginBottom: 16 }}>
            {t("signin.tagline")}
          </div>
          <h1 className="disp" style={{ fontSize: 64, margin: 0, color: "#F4F3EE" }}>
            {t("signin.h1.a")}
            <br />
            {t("signin.h1.b")} <span style={{ color: "#FFE66B" }}>{t("signin.h1.c")}</span>
          </h1>
          <p style={{ margin: "18px 0 0", fontSize: 16, lineHeight: 1.6, color: "#B9B7D6", maxWidth: 430 }}>{t("signin.lede")}</p>
        </div>
      </section>

      <section className="signin-panel">
        <div className="row jb">
          <span className="eyebrow">{t(creating ? "signin.eyebrow.create" : "signin.eyebrow.signin")}</span>
          <LanguageSwitch />
        </div>

        <form className="signin-form" onSubmit={onSubmit} noValidate>
          <h2 className="disp" style={{ fontSize: 40, margin: "0 0 8px" }}>
            {t(creating ? "signin.title.create" : "signin.title.signin")}
          </h2>
          <p className="t2 s15" style={{ margin: "0 0 28px" }}>
            {t(creating ? "signin.sub.create" : "signin.sub.signin")}
          </p>

          <div className="col g16">
            {formError && (
              <div className="form-error" role="alert">
                <Icon name="warning" size={16} />
                <span>{t(formError)}</span>
              </div>
            )}
            <div>
              <label className="lbl" htmlFor="email">
                {t("signin.email")}
              </label>
              <input
                id="email"
                className="inp"
                type="email"
                name="email"
                autoComplete="username"
                autoFocus
                spellCheck={false}
                value={email}
                aria-invalid={touched && emailError ? true : undefined}
                aria-describedby={touched && emailError ? "email-error" : undefined}
                onChange={(event) => setEmail(event.target.value)}
              />
              {touched && emailError && (
                <div id="email-error" className="field-error">
                  {t(emailError)}
                </div>
              )}
            </div>
            <div>
              <label className="lbl" htmlFor="password">
                {t("signin.password")}
              </label>
              <input
                id="password"
                className="inp"
                type="password"
                name="password"
                autoComplete={creating ? "new-password" : "current-password"}
                value={password}
                aria-invalid={touched && passwordError ? true : undefined}
                aria-describedby={touched && passwordError ? "password-error" : undefined}
                onChange={(event) => setPassword(event.target.value)}
              />
              {creating && !(touched && passwordError) && <div className="help">{t("signin.passwordHelp", { n: MIN_PASSWORD })}</div>}
              {touched && passwordError && (
                <div id="password-error" className="field-error">
                  {t(passwordError, { n: MIN_PASSWORD })}
                </div>
              )}
            </div>
            <button type="submit" className="btn pri lg" style={{ width: "100%" }} disabled={busy} aria-busy={busy}>
              {t(creating ? "signin.submit.create" : "signin.submit.signin")}
              <Icon name="arrow" size={18} flip />
            </button>
          </div>
        </form>

        <div className="row jb s13 t2">
          <span>
            {t(creating ? "signin.switch.toSignInPrompt" : "signin.switch.toCreatePrompt")}{" "}
            <button type="button" className="link b6" onClick={() => switchMode(creating ? "signin" : "create")}>
              {t(creating ? "signin.switch.toSignIn" : "signin.switch.toCreate")}
            </button>
          </span>
          <span className="t3">{t("signin.footnote")}</span>
        </div>
      </section>
    </div>
  );
}
