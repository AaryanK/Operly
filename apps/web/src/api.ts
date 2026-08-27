function cookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  const value = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : null;
}

export function csrfToken(): string | null {
  return cookie("__Host-operly_csrf") || cookie("operly_csrf") || cookie("operly_preauth_csrf");
}

export class ApiError extends Error {
  status: number;
  code?: string;
  details?: unknown;

  constructor(message: string, status: number, code?: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function readResponse<T>(response: Response): Promise<T> {
  if (response.status === 401) window.dispatchEvent(new Event("operly:logout"));
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    let code: string | undefined;
    let details: unknown;
    try {
      const body = await response.json();
      details = body?.detail ?? body;
      const candidate = body?.detail?.message ?? body?.detail ?? body?.message;
      message = typeof candidate === "string" ? candidate : message;
      code = typeof body?.detail?.code === "string" ? body.detail.code : typeof body?.code === "string" ? body.code : undefined;
    } catch {
      // Keep the status fallback when the server did not return JSON.
    }
    throw new ApiError(message, response.status, code, details);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function authorizedHeaders(options: RequestInit): Headers {
  const headers = new Headers(options.headers);
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = csrfToken();
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  return headers;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = authorizedHeaders(options);
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`/api${path}`, { ...options, headers, credentials: "same-origin" });
  return readResponse<T>(response);
}

export async function apiForm<T>(path: string, form: FormData, options: RequestInit = {}): Promise<T> {
  const headers = authorizedHeaders({ ...options, method: options.method || "POST" });
  const response = await fetch(`/api${path}`, {
    ...options,
    method: options.method || "POST",
    body: form,
    headers,
    credentials: "same-origin",
  });
  return readResponse<T>(response);
}
