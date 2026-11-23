import { vi } from "vitest";

export type Route = unknown | ((init?: RequestInit) => { status?: number; body?: unknown });

/** Substitui `fetch` por um roteador simples "METODO /caminho" -> resposta. Sem rede. */
export function mockApi(routes: Record<string, Route>) {
  const calls: { method: string; path: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const path = String(input);
    calls.push({ method, path, init });
    const route = routes[`${method} ${path}`];
    if (route === undefined) return new Response(JSON.stringify({ detail: "não mapeado" }), { status: 404 });
    const out = typeof route === "function" ? (route as (i?: RequestInit) => { status?: number; body?: unknown })(init) : { body: route };
    return new Response(JSON.stringify(out.body ?? null), {
      status: out.status ?? 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return { calls, fn };
}
