import { useCallback, useEffect, useState } from "react";
import { api, setUnauthorizedHandler } from "./api";
import { IncidentList, IncidentView } from "./pages/Incidentes";
import { Integracoes } from "./pages/Integracoes";
import { Login } from "./pages/Login";
import { Precisao } from "./pages/Precisao";
import { Risco } from "./pages/Risco";
import { S } from "./strings.pt";

type Route = { page: "incidentes" | "risco" | "precisao" | "integracoes"; id?: number };

/** Rotas por hash (#/incidentes/42): não colidem com as rotas da API no mesmo servidor. */
export function parseRoute(hash: string): Route {
  const [, page, rest] = hash.replace(/^#/, "").split("/");
  if (page === "risco" || page === "precisao" || page === "integracoes") return { page };
  const id = Number(rest);
  return page === "incidentes" && rest && Number.isInteger(id) && id > 0 ? { page: "incidentes", id } : { page: "incidentes" };
}

const NAV: { page: Route["page"]; label: string }[] = [
  { page: "incidentes", label: S.nav.incidentes },
  { page: "risco", label: S.nav.risco },
  { page: "precisao", label: S.nav.precisao },
  { page: "integracoes", label: S.nav.integracoes },
];

export function App() {
  const [user, setUser] = useState<string | null | undefined>(undefined); // undefined = verificando
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));

  const check = useCallback(() => {
    api.me().then((m) => setUser(m.user), () => setUser(null));
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    check();
  }, [check]);

  useEffect(() => {
    const on = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  if (user === undefined) return <p className="muted pad">{S.app.carregando}</p>;
  if (user === null) return <Login onLoggedIn={check} />;

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="principal">
        <div className="brand">{S.app.nome}</div>
        {NAV.map((n) => (
          <a key={n.page} href={`#/${n.page}`} aria-current={route.page === n.page ? "page" : undefined}>
            {n.label}
          </a>
        ))}
        <div className="spacer" />
        <span className="muted">{S.nav.usuario}: {user}</span>
        <button
          onClick={() => {
            void api.logout().finally(() => setUser(null));
          }}
        >
          {S.nav.sair}
        </button>
      </nav>
      <main className="content">
        {route.page === "incidentes" && (route.id ? <IncidentView id={route.id} /> : <IncidentList />)}
        {route.page === "risco" && <Risco />}
        {route.page === "precisao" && <Precisao />}
        {route.page === "integracoes" && <Integracoes />}
      </main>
    </div>
  );
}
