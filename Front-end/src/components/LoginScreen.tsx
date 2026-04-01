import type { FormEvent } from "react";
import type { AsyncState } from "../types";

type LoginScreenProps = {
  usernameInput: string;
  onUsernameChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  status: AsyncState;
  error: string;
};

export function LoginScreen({
  usernameInput,
  onUsernameChange,
  onSubmit,
  status,
  error,
}: LoginScreenProps) {
  return (
    <main className="auth-page">
      <section className="auth-panel">
        <div className="auth-copy">
          <p className="eyebrow">AI Linux Assistant</p>
          <h1>Sign in to enter your workspace.</h1>
          <p>Projects, chats, and memory live behind a named workspace. Pick a username to continue.</p>
        </div>

        <form className="auth-form" onSubmit={onSubmit}>
          <label className="stack">
            <span className="label">Username</span>
            <input
              value={usernameInput}
              onChange={(event) => onUsernameChange(event.target.value)}
              placeholder="kayne19"
              autoFocus
            />
          </label>
          {error ? <p className="error-banner auth-error">{error}</p> : null}
          <button type="submit" disabled={status === "loading" || !usernameInput.trim()}>
            {status === "loading" ? "Entering..." : "Enter workspace"}
          </button>
        </form>
      </section>
    </main>
  );
}
