import React, { ErrorInfo, ReactNode } from "react";

type AppErrorBoundaryProps = {
  children: ReactNode;
};

type AppErrorBoundaryState = {
  hasError: boolean;
};

export class AppErrorBoundary extends React.Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = {
    hasError: false,
  };

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("App shell render failed.", error, errorInfo);
  }

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <main className="auth-page">
        <section className="auth-panel">
          <div className="auth-copy">
            <p className="eyebrow">AI Linux Assistant</p>
            <h1>The app hit a render error.</h1>
            <p>Reload the page to recover the current session.</p>
          </div>
          <div className="auth-form">
            <button type="button" className="auth-btn" onClick={() => window.location.reload()}>
              Reload app
            </button>
          </div>
        </section>
      </main>
    );
  }
}
