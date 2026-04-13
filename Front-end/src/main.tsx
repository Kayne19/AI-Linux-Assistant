import React from "react";
import ReactDOM from "react-dom/client";
import { Auth0Provider } from "@auth0/auth0-react";
import App from "./App";
import { AppErrorBoundary } from "./components/AppErrorBoundary";
import { readAuth0Config } from "./authConfig";
import "./styles.css";

const auth0 = readAuth0Config();

const root = ReactDOM.createRoot(document.getElementById("root")!);

if (!auth0.config) {
  root.render(
    <React.StrictMode>
      <AppErrorBoundary>
        <main className="auth-page">
          <section className="auth-panel">
            <div className="auth-copy">
              <p className="eyebrow">AI Linux Assistant</p>
              <h1>Auth0 configuration is missing.</h1>
              <p>{auth0.error}</p>
            </div>
          </section>
        </main>
      </AppErrorBoundary>
    </React.StrictMode>,
  );
} else {
  root.render(
    <React.StrictMode>
      <AppErrorBoundary>
        <Auth0Provider
          domain={auth0.config.domain}
          clientId={auth0.config.clientId}
          authorizationParams={{
            audience: auth0.config.audience,
            redirect_uri: auth0.config.redirectUri,
            scope: "openid profile email",
          }}
          cacheLocation="memory"
        >
          <App />
        </Auth0Provider>
      </AppErrorBoundary>
    </React.StrictMode>,
  );
}
