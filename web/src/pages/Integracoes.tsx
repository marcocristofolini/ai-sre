import { api } from "../api";
import { Async } from "../components";
import { S } from "../strings.pt";
import { useApi } from "../useApi";

export function Integracoes() {
  const state = useApi(api.integrations);
  return (
    <section>
      <header className="page-head">
        <h1>{S.integracoes.titulo}</h1>
      </header>
      <p className="muted">{S.integracoes.descricao}</p>
      <Async state={state}>
        {(items) => (
          <ul className="list">
            {items.map((i) => (
              <li key={i.id} className="card row">
                <strong>{S.integracoes.nomes[i.id] ?? i.id}</strong>
                <span className={i.configured ? "ok" : "muted"}>
                  {i.configured ? S.integracoes.configurada : S.integracoes.naoConfigurada}
                </span>
                {i.detail && <span className="muted">{i.detail}</span>}
              </li>
            ))}
          </ul>
        )}
      </Async>
    </section>
  );
}
