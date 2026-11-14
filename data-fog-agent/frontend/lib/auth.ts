// The access token lives only in memory. On a page reload it is gone, and we get a
// new one from the refresh cookie, which JavaScript can't read.
let accessToken: string | null = null;

export type User = {
  id: number;
  org_id: number;
  email: string;
  name: string;
  role: "admin" | "member";
};

export async function login(email: string, password: string): Promise<boolean> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) return false;

  accessToken = (await response.json()).access_token;
  return true;
}

async function refreshAccessToken(): Promise<boolean> {
  const response = await fetch("/api/auth/refresh", { method: "POST" });
  if (!response.ok) return false;

  accessToken = (await response.json()).access_token;
  return true;
}

export async function logout() {
  accessToken = null;
  await fetch("/api/auth/logout", { method: "POST" });
}

/** fetch() for backend calls. Adds the token and refreshes it once if it has expired. */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  if (!accessToken) await refreshAccessToken();

  const send = () =>
    fetch(path, {
      ...init,
      headers: { ...init.headers, Authorization: `Bearer ${accessToken}` },
    });

  const response = await send();
  if (response.status !== 401) return response;

  if (!(await refreshAccessToken())) return response;
  return send();
}

export async function getCurrentUser(): Promise<User | null> {
  const response = await apiFetch("/api/auth/me");
  return response.ok ? response.json() : null;
}
