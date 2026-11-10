/**
 * Same-origin proxy from the dashboard to the backend management API.
 *
 * The browser only ever talks to the dashboard's origin, which lets the session
 * cookie be HttpOnly and SameSite=Strict without any CORS configuration.
 *
 * Two deliberate restrictions:
 *  - the agent gateway is not reachable through the dashboard origin;
 *  - an Authorization header is never forwarded, so this proxy cannot carry
 *    agent credentials.
 */
import type { NextRequest } from "next/server";

const BACKEND_URL = (process.env.ACT_BACKEND_URL ?? "http://localhost:8000").replace(/\/$/, "");
const FORWARDED_REQUEST_HEADERS = ["accept", "content-type", "cookie", "origin", "x-correlation-id"];
const FORWARDED_RESPONSE_HEADERS = ["content-type", "x-correlation-id"];

function json(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message } }, { status });
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (path[0] !== "v1" || path[1] === "gateway" || path.some((part) => part === ".." || part === ".")) {
    return json(404, "not_found", "Not found.");
  }

  const target = `${BACKEND_URL}/api/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    return json(502, "backend_unavailable", "The Control Tower API cannot be reached.");
  }

  const responseHeaders = new Headers({ "cache-control": "no-store" });
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  for (const cookie of upstream.headers.getSetCookie()) {
    responseHeaders.append("set-cookie", cookie);
  }
  const body = upstream.status === 204 || upstream.status === 304 ? null : await upstream.arrayBuffer();
  return new Response(body, { status: upstream.status, headers: responseHeaders });
}

export const dynamic = "force-dynamic";
export { proxy as GET, proxy as POST, proxy as PATCH, proxy as PUT, proxy as DELETE };
