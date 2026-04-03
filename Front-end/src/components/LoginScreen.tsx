import { useState } from "react";
import type { AsyncState } from "../types";

type LoginScreenProps = {
  onSignIn: () => void | Promise<void>;
  onSignUp: () => void | Promise<void>;
  status: AsyncState;
  error: string;
};

export function LoginScreen({ onSignIn, onSignUp, status, error }: LoginScreenProps) {
  const [pending, setPending] = useState(false);
  const busy = pending || status === "loading";

  async function handleSignIn() {
    setPending(true);
    try {
      await onSignIn();
    } finally {
      setPending(false);
    }
  }

  async function handleSignUp() {
    setPending(true);
    try {
      await onSignUp();
    } finally {
      setPending(false);
    }
  }

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

          <div className="auth-hero">
            <h2 className="auth-hero-headline">Your Linux troubleshooting workspace.</h2>
            <p className="auth-tagline">
              Persistent, project-scoped AI assistance that remembers context and builds on prior work.
            </p>
            <ul className="auth-feature-list">
              <li>Projects with session-persistent memory</li>
              <li>Multi-agent council deliberation</li>
              <li>Context that carries across conversations</li>
            </ul>
          </div>
        </aside>

        <section className="auth-panel">
          <div className="auth-copy">
            <h1>Sign in to your workspace</h1>
            <p>Your projects, chats, and context are ready when you are.</p>
          </div>

          <div className="auth-form">
            {error ? <p className="error-banner auth-error">{error}</p> : null}
            <button
              type="button"
              className="auth-btn"
              onClick={() => void handleSignIn()}
              disabled={busy}
            >
              {busy ? (
                <span className="auth-btn-inner">
                  <span className="auth-spinner" aria-hidden="true" />
                  Redirecting…
                </span>
              ) : (
                "Sign in"
              )}
            </button>
            <button
              type="button"
              className="auth-link-btn"
              onClick={() => void handleSignUp()}
              disabled={busy}
            >
              New here? Create an account
            </button>
          </div>
        </section>
      </div>
    </main>
  );
}
