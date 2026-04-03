import { useAuth0 } from "@auth0/auth0-react";
import { startTransition, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { clearApiAuth, configureApiAuth } from "../apiAuth";
import { readAuth0Config } from "../authConfig";
import type { AppBootstrapResponse, User } from "../types";

const auth0Config = readAuth0Config();

export function useAuth() {
  const { logout: auth0Logout } = useAuth0();
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const accessTokenRef = useRef<string | null>(null);
  const [bootstrap, setBootstrap] = useState<AppBootstrapResponse | null>(null);
  const [bootstrapLoading, setBootstrapLoading] = useState(false);
  const [bootstrapError, setBootstrapError] = useState("");
  const [forcedSignedOut, setForcedSignedOut] = useState(false);

  useEffect(() => {
    accessTokenRef.current = accessToken;
  }, [accessToken]);

  useEffect(() => {
    configureApiAuth({
      getAccessToken: async () => accessTokenRef.current ?? "",
      onUnauthorized: () => {
        setForcedSignedOut(true);
        setAccessToken(null);
        setBootstrap(null);
        setBootstrapError("Your session is no longer valid. Sign in again.");
      },
    });
    return () => {
      clearApiAuth();
    };
  }, []);

  useEffect(() => {
    if (!accessToken || forcedSignedOut) {
      setBootstrap(null);
      setBootstrapLoading(false);
      return;
    }

    let cancelled = false;
    setBootstrapLoading(true);
    setBootstrapError("");
    void api.appBootstrap()
      .then((result) => {
        if (cancelled) return;
        startTransition(() => {
          setBootstrap(result);
        });
      })
      .catch((error: Error) => {
        if (cancelled) return;
        setBootstrap(null);
        setBootstrapError(error.message);
      })
      .finally(() => {
        if (!cancelled) {
          setBootstrapLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [accessToken, forcedSignedOut]);

  async function signIn(email: string, password: string) {
    const config = auth0Config.config;
    if (!config) {
      setBootstrapError(auth0Config.error || "Auth0 not configured.");
      return;
    }

    setForcedSignedOut(false);
    setBootstrapError("");

    const res = await fetch(`https://${config.domain}/oauth/token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        grant_type: "password",
        username: email,
        password,
        audience: config.audience,
        client_id: config.clientId,
        scope: "openid profile email",
      }),
    });

    const data = await res.json() as { access_token?: string; error_description?: string; error?: string };

    if (!res.ok) {
      throw new Error(data.error_description || data.error || "Invalid credentials.");
    }

    setAccessToken(data.access_token ?? null);
  }

  function logout() {
    clearApiAuth();
    setForcedSignedOut(false);
    setAccessToken(null);
    setBootstrap(null);
    setBootstrapError("");
    auth0Logout({
      logoutParams: {
        returnTo: window.location.origin,
      },
    });
  }

  const user: User | null = bootstrap?.user || null;

  return {
    user,
    bootstrap,
    loading: bootstrapLoading,
    error: bootstrapError,
    isSignedIn: Boolean(user) && !forcedSignedOut,
    signIn,
    logout,
  };
}
