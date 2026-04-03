import type { AsyncState } from "../types";

type LoginScreenProps = {
  onSignIn: () => void | Promise<void>;
  status: AsyncState;
  error: string;
};

export function LoginScreen({ onSignIn, status, error }: LoginScreenProps) {
  return (
    <main className="auth-page">
      <section className="auth-panel">
        <div className="auth-copy">
          <p className="eyebrow">AI Linux Assistant</p>
          <h1>Sign in to enter your workspace.</h1>
          <p>Projects, chats, and memory are scoped server-side. Web access now goes through Auth0.</p>
        </div>

        <div className="auth-form">
          {error ? <p className="error-banner auth-error">{error}</p> : null}
          <button type="button" onClick={() => void onSignIn()} disabled={status === "loading"}>
            {status === "loading" ? "Redirecting..." : "Continue with Auth0"}
          </button>
        </div>
      </section>
    </main>
  );
}
