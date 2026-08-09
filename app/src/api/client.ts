/**
 * HTTP client for the API. One door for every request.
 *
 * Paths are **relative** (`/api/...`) on purpose: in production the API serves
 * the build from its own origin (F3.7), and in development Vite's proxy
 * forwards `/api` to wherever `VITE_API_TARGET` says. With an absolute URL the
 * origin would have to be configured in two places and kept in agreement.
 */

/** An error the API explained. `status` is the HTTP code. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  /**
   * @param status - HTTP status code, or 0 when the request never reached the API.
   * @param message - Human-readable message, already in the interface language.
   * @param detail - Raw error body, kept for callers that need to inspect it.
   */
  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  /**
   * Whether the request itself was at fault and retrying is pointless.
   *
   * A 4xx is the request's fault: sending it again gives the same result.
   *
   * @return True for any 4xx status.
   */
  get isClientError() {
    return this.status >= 400 && this.status < 500;
  }
}

type Params = Record<string, string | number | boolean | undefined | null>;

interface Options {
  params?: Params;
  body?: unknown;
  signal?: AbortSignal;
}

/**
 * Pulls a readable sentence out of a FastAPI error body.
 *
 * It comes in two shapes and both have to be covered: `{"detail": "text"}` for
 * the errors we raise ourselves, and `{"detail": [{loc, msg}, …]}` for
 * Pydantic's validation 422s. Without flattening the second, the 41-field form
 * of F6.8 would say "[object Object]" exactly when the user needs to know which
 * field they got wrong.
 *
 * @param status - HTTP status code, used for the fallback message.
 * @param body - Parsed response body, or the raw text when it was not JSON.
 * @return A message ready to be shown on screen.
 */
function errorMessage(status: number, body: unknown): string {
  if (typeof body === "string" && body.trim()) return body;

  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const parts = detail.map((entry) => {
        if (!entry || typeof entry !== "object") return String(entry);
        const { loc, msg } = entry as { loc?: unknown[]; msg?: string };
        // `loc` starts with "body"/"query", which tells nobody anything: keep
        // the rest, which is the field name.
        const field = Array.isArray(loc) ? loc.slice(1).join(".") : "";
        return field ? `${field}: ${msg ?? ""}`.trim() : (msg ?? "");
      });
      if (parts.length) return parts.join("; ");
    }
  }
  return `La API respondio ${status}.`;
}

/**
 * Appends a query string, dropping entries that carry no value.
 *
 * @param path - Relative API path.
 * @param params - Query parameters. Undefined, null and empty values are skipped
 *     so an absent filter never reaches the API as `?symbol=`.
 * @return The path with its query string, or unchanged when nothing was kept.
 */
function buildUrl(path: string, params?: Params): string {
  if (!params) return path;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    query.set(key, String(value));
  }
  const string = query.toString();
  return string ? `${path}?${string}` : path;
}

/**
 * Performs one HTTP request against the API and returns its parsed body.
 *
 * @template T - Shape of the response body, taken from `api/types.ts`.
 * @param method - HTTP verb.
 * @param path - Relative API path, always starting with `/api`.
 * @param options - Request options.
 * @param options.params - Query parameters.
 * @param options.body - Value to send as JSON. Undefined sends no body.
 * @param options.signal - Signal used to abort the request.
 * @return The parsed body, or undefined for a 204.
 * @throws {ApiError} When the API answers with a non-2xx status, or with status
 *     0 when the request never reached it.
 * @throws {DOMException} When the request was aborted through `signal`.
 */
async function request<T>(
  method: string,
  path: string,
  { params, body, signal }: Options = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(buildUrl(path, params), {
      method,
      signal,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    // There is no response here: the server is down, or the network dropped.
    // It is distinguished from an error with a code because the screen says
    // something else.
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "No se pudo contactar con la API.", cause);
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let parsed: unknown = text;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      // Keep the raw text: a strange message beats swallowing the error.
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, errorMessage(response.status, parsed), parsed);
  }
  return parsed as T;
}

/** The one door to the API. Every verb throws {@link ApiError} on failure. */
export const api = {
  get: <T>(path: string, params?: Params, signal?: AbortSignal) =>
    request<T>("GET", path, { params, signal }),
  post: <T>(path: string, body?: unknown, params?: Params) =>
    request<T>("POST", path, { body, params }),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, { body }),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, { body }),
  delete: <T>(path: string, params?: Params) => request<T>("DELETE", path, { params }),
};
