import { DecisionGraph } from "./DecisionGraph";

function LogoMark() {
  return (
    <svg className="logo-mark" viewBox="0 0 32 32" aria-hidden="true">
      <g fill="currentColor">
        <rect x="14.2" y="3" width="3.6" height="26" rx="1.8" />
        <rect
          x="14.2"
          y="3"
          width="3.6"
          height="26"
          rx="1.8"
          transform="rotate(45 16 16)"
        />
        <rect x="3" y="14.2" width="26" height="3.6" rx="1.8" />
        <rect
          x="3"
          y="14.2"
          width="26"
          height="3.6"
          rx="1.8"
          transform="rotate(45 16 16)"
        />
      </g>
    </svg>
  );
}

function GlobeIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="8.2" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <path
        d="M3.8 12h16.4M12 3.8c2.3 2.5 3.5 5.2 3.5 8.2s-1.2 5.7-3.5 8.2c-2.3-2.5-3.5-5.2-3.5-8.2s1.2-5.7 3.5-8.2z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
      />
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M3 8h10M9.2 4.2 13 8l-3.8 3.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function GoogleMark() {
  return (
    <svg viewBox="0 0 18 18" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.91c1.7-1.57 2.69-3.88 2.69-6.62z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.91-2.26c-.81.54-1.84.86-3.05.86-2.34 0-4.33-1.58-5.04-3.71H.96v2.33A9 9 0 0 0 9 18z"
      />
      <path
        fill="#FBBC05"
        d="M3.96 10.71A5.41 5.41 0 0 1 3.68 9c0-.59.1-1.17.28-1.71V4.96H.96A9 9 0 0 0 0 9c0 1.45.35 2.83.96 4.04l3-2.33z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.51.45 3.44 1.35l2.58-2.59C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.96l3 2.33C4.67 5.16 6.66 3.58 9 3.58z"
      />
    </svg>
  );
}

function MicrosoftMark() {
  return (
    <svg viewBox="0 0 18 18" aria-hidden="true">
      <rect x="0" y="0" width="8.2" height="8.2" fill="#F25022" />
      <rect x="9.8" y="0" width="8.2" height="8.2" fill="#7FBA00" />
      <rect x="0" y="9.8" width="8.2" height="8.2" fill="#00A4EF" />
      <rect x="9.8" y="9.8" width="8.2" height="8.2" fill="#FFB900" />
    </svg>
  );
}

export function SignInScreen() {
  return (
    <main className="signin">
      <section className="signin-brand" aria-label="Edrak">
        <div className="logo">
          <LogoMark />
          <span>edrak</span>
        </div>

        <div className="stage-wrap">
          <div className="stage-scale">
            <DecisionGraph />
          </div>
        </div>

        <div className="brand-copy">
          <p className="eyebrow">Agentic decision intelligence</p>
          <h1>
            Evidence first.
            <br />
            Decisions <span>yours.</span>
          </h1>
          <p>
            Four specialist workers investigate. Verification checks what they
            found. You approve the plan and record the decision.
          </p>
        </div>
      </section>

      <section className="signin-panel" aria-label="Sign in">
        <header className="panel-top">
          <span className="kicker">Sign in</span>
          <button className="lang" type="button">
            EN
            <GlobeIcon />
          </button>
        </header>

        <div className="panel-main">
          <div className="form">
            <div className="welcome">
              <h2>Welcome back.</h2>
              <p>Sign in to your workspace.</p>
            </div>

            <div className="field">
              <label htmlFor="work-email">Work email</label>
              <input
                id="work-email"
                name="email"
                type="email"
                autoComplete="username"
                autoFocus
                defaultValue="layla@niledger.example"
                spellCheck={false}
              />
            </div>

            <div className="field">
              <div className="field-head">
                <label htmlFor="password">Password</label>
                <button className="text-button" type="button">
                  Forgot?
                </button>
              </div>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                defaultValue="niledger"
              />
            </div>

            <button className="continue" type="button">
              Continue
              <ArrowIcon />
            </button>

            <div className="or" aria-hidden="true">
              <span>OR</span>
            </div>

            <button className="social" type="button">
              <GoogleMark />
              Continue with Google
            </button>
            <button className="social" type="button">
              <MicrosoftMark />
              Continue with Microsoft
            </button>
          </div>
        </div>

        <footer className="panel-foot">
          <p>
            New to Edrak?{" "}
            <button className="text-button" type="button">
              Create a workspace
            </button>
          </p>
          <p className="foot-note">Edrak informs decisions. People make them.</p>
        </footer>
      </section>
    </main>
  );
}
