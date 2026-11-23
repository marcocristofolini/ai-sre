import { api } from "../api";
import { Async, formatScore } from "../components";
import { S } from "../strings.pt";
import { useApi } from "../useApi";

export function Risco() {
  const state = useApi(api.risk, [], 15_000);
  return (
    <section>
      <header className="page-head">
        <h1>{S.risco.titulo}</h1>
      </header>
      <p className="muted">{S.risco.descricao}</p>
      <Async state={state}>
        {(rows) =>
          rows.length === 0 ? (
            <p className="muted">{S.risco.vazio}</p>
          ) : (
            <table className="card">
              <thead>
                <tr>
                  <th>{S.risco.workload}</th>
                  <th>{S.risco.estadoCol}</th>
                  <th>{S.risco.scoreCol}</th>
                  <th>{S.risco.limiar}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.workload} className={r.state === "ativo" && r.score > r.threshold ? "hot" : ""}>
                    <td>{r.workload}</td>
                    <td>{S.risco.estado[r.state] ?? r.state}</td>
                    <td>{r.state === "ativo" ? formatScore(r.score) : "—"}</td>
                    <td>{formatScore(r.threshold)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </Async>
    </section>
  );
}
