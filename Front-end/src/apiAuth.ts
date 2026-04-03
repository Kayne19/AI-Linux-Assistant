type TokenProvider = () => Promise<string>;
type UnauthorizedHandler = () => void;

let accessTokenProvider: TokenProvider | null = null;
let unauthorizedHandler: UnauthorizedHandler | null = null;

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function configureApiAuth(options: {
  getAccessToken: TokenProvider;
  onUnauthorized?: UnauthorizedHandler;
}) {
  accessTokenProvider = options.getAccessToken;
  unauthorizedHandler = options.onUnauthorized || null;
}

export function clearApiAuth() {
  accessTokenProvider = null;
  unauthorizedHandler = null;
}

export async function getAuthorizationHeader(required = true): Promise<Record<string, string>> {
  if (!required) {
    return {};
  }
  if (!accessTokenProvider) {
    throw new ApiError(401, "Authentication is not ready.");
  }
  const accessToken = (await accessTokenProvider()).trim();
  if (!accessToken) {
    throw new ApiError(401, "Authentication token was unavailable.");
  }
  return { Authorization: `Bearer ${accessToken}` };
}

export async function handleUnauthorizedStatus(status: number) {
  if (status === 401) {
    unauthorizedHandler?.();
  }
}

export function isApiErrorStatus(error: unknown, status: number) {
  return error instanceof ApiError && error.status === status;
}
