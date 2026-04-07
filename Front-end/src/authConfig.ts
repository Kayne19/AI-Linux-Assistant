export type Auth0Config = {
  domain: string;
  clientId: string;
  audience: string;
  redirectUri: string;
};

type LocationLike = {
  origin?: string;
  protocol?: string;
  hostname?: string;
};

function isSecureAuth0Origin(locationLike?: LocationLike | null): boolean {
  const protocol = (locationLike?.protocol || "").trim().toLowerCase();
  const hostname = (locationLike?.hostname || "").trim().toLowerCase();
  if (protocol === "https:") {
    return true;
  }
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1" || hostname === "[::1]";
}

export function readAuth0Config(
  env: Record<string, string | undefined> | undefined = (import.meta as ImportMeta & {
    env?: Record<string, string | undefined>;
  }).env,
  locationLike: LocationLike | undefined = typeof window !== "undefined" ? window.location : undefined,
): { config: Auth0Config | null; error: string } {
  const defaultRedirectUri = locationLike?.origin || "";
  const domain = (env?.VITE_AUTH0_DOMAIN || "").trim();
  const clientId = (env?.VITE_AUTH0_CLIENT_ID || "").trim();
  const audience = (env?.VITE_AUTH0_AUDIENCE || "").trim();
  const redirectUri = (env?.VITE_AUTH0_REDIRECT_URI || defaultRedirectUri).trim();

  const missing = [
    !domain ? "VITE_AUTH0_DOMAIN" : "",
    !clientId ? "VITE_AUTH0_CLIENT_ID" : "",
    !audience ? "VITE_AUTH0_AUDIENCE" : "",
  ].filter(Boolean);

  if (missing.length) {
    return {
      config: null,
      error: `Auth0 is not configured. Missing: ${missing.join(", ")}.`,
    };
  }

  if (!isSecureAuth0Origin(locationLike)) {
    return {
      config: null,
      error: `Auth0 SPA auth requires HTTPS or localhost. Current origin '${locationLike?.origin || "unknown"}' is not allowed. Open the app via http://localhost:5173 for local dev, or serve it over HTTPS.`,
    };
  }

  return {
    config: {
      domain,
      clientId,
      audience,
      redirectUri,
    },
    error: "",
  };
}
