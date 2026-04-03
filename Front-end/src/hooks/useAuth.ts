import { useAuth0 } from "@auth0/auth0-react";
import { startTransition, useEffect, useState } from "react";
import { api } from "../api";
import { clearApiAuth, configureApiAuth } from "../apiAuth";
import { readAuth0Config } from "../authConfig";
import type { AppBootstrapResponse, User } from "../types";

const auth0Config = readAuth0Config();

export function useAuth() {
  const { isLoading, isAuthenticated, loginWithRedirect, logout: auth0Logout, getAccessTokenSilently } = useAuth0();
  const [bootstrap, setBootstrap] = useState<AppBootstrapResponse | null>(null);
  const [bootstrapLoading, setBootstrapLoading] = useState(false);
  const [bootstrapError, setBootstrapError] = useState("");
  const [forcedSignedOut, setForcedSignedOut] = useState(false);

  useEffect(() => {
    configureApiAuth({
      getAccessToken: async () =>
        getAccessTokenSilently({
          authorizationParams: {
            audience: auth0Config.config?.audience || "",
          },
        }),
      onUnauthorized: () => {
        setForcedSignedOut(true);
        setBootstrap(null);
        setBootstrapError("Your session is no longer valid. Sign in again.");
      },
    });
    return () => {
      clearApiAuth();
    };
  }, [getAccessTokenSilently]);

  useEffect(() => {
    if (isLoading) {
      return;
    }
    if (!isAuthenticated || forcedSignedOut) {
      setBootstrap(null);
      setBootstrapLoading(false);
      return;
    }

    let cancelled = false;
    setBootstrapLoading(true);
    setBootstrapError("");
    void api.appBootstrap()
      .then((result) => {
        if (cancelled) {
          return;
        }
        startTransition(() => {
          setBootstrap(result);
        });
      })
      .catch((error: Error) => {
        if (cancelled) {
          return;
        }
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
  }, [forcedSignedOut, isAuthenticated, isLoading]);

  async function signIn() {
    setForcedSignedOut(false);
    setBootstrapError("");
    await loginWithRedirect();
  }

  function logout() {
    clearApiAuth();
    setForcedSignedOut(false);
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
    loading: isLoading || (isAuthenticated && !forcedSignedOut && bootstrapLoading),
    error: bootstrapError,
    isSignedIn: Boolean(user) && !forcedSignedOut,
    signIn,
    logout,
  };
}
