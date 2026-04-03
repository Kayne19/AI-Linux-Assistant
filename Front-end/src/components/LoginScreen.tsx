import type { AsyncState } from "../types";

type LoginScreenProps = {
  onSignIn: () => void | Promise<void>;
  status: AsyncState;
  error: string;
};

export function LoginScreen({ onSignIn, status, error }: LoginScreenProps) {
  return (
    <main className="auth-page">
      <div className="auth-layout">
        <aside className="auth-visual">
          <div className="auth-brand">
            <svg className="auth-logo" viewBox="0 0 32 32" fill="none" aria-hidden="true">
              <rect width="32" height="32" rx="8" fill="currentColor" fillOpacity="0.12" />
              <path d="M8 11h3l2 4 2-7 2 7 2-4h3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M8 21l4-4 3 3 5-6 4 7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" strokeOpacity="0.55" />
            </svg>
            <span className="auth-wordmark">AI Linux Assistant</span>
          </div>
          <p className="auth-tagline">Stateful troubleshooting anchored to projects, not disposable chats.</p>
        </aside>

        <section className="auth-panel">
          <div className="auth-copy">
            <h1>Sign in to enter your workspace.</h1>
            <p>Projects, chats, and memory are scoped server-side.</p>
          </div>

          <div className="auth-form">
            {error ? <p className="error-banner auth-error">{error}</p> : null}
            <button type="button" className="auth-btn" onClick={() => void onSignIn()} disabled={status === "loading"}>
              {status === "loading" ? "Redirecting..." : "Continue with Auth0"}
            </button>
          </div>
        </section>
      </div>
    </main>
  );
}
