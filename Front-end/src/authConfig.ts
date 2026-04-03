export type Auth0Config = {
  domain: string;
  clientId: string;
  audience: string;
  redirectUri: string;
};

export function readAuth0Config(
  env: Record<string, string | undefined> | undefined = (import.meta as ImportMeta & {
    env?: Record<string, string | undefined>;
  }).env,
): { config: Auth0Config | null; error: string } {
  const defaultRedirectUri = typeof window !== "undefined" ? window.location.origin : "";
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
