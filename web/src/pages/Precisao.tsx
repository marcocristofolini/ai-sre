import { api } from "../api";
import { Async } from "../components";
import { S } from "../strings.pt";
import { useApi } from "../useApi";

export function Precisao() {
  const state = useApi(api.scoreboard);
  return (
    <section>
      <header className="page-head">
        <h1>{S.precisao.titulo}</h1>
      </header>
      <Async state={state}>
        {(board) => {
          if (!board.measured) {
            return <p className="card muted">{board.reason === "invalid" ? S.precisao.invalido : S.precisao.semMedicao}</p>;
          }
          return (
            <>
              <div className="card">
                <p className="muted">{S.precisao.aviso}</p>
                <p>
                  <span className="tag">{board.label}</span>
                </p>
                <dl className="facts">
                  <dt>{S.precisao.corretos}</dt>
                  <dd className="big">{`${board.correct}/${board.total}`}</dd>
                  <dt>{S.precisao.modelo}</dt>
                  <dd>{board.model}</dd>
                </dl>
                {board.injection_errors > 0 && <p className="muted">{S.precisao.errosInjecao(board.injection_errors)}</p>}
              </div>
              <div className="card">
                <h2>{S.precisao.calibracao}</h2>
                {board.calibration.length === 0 ? (
                  <p className="muted">{S.precisao.semCalibracao}</p>
                ) : (
                  <>
                    <p className="muted">{S.precisao.calibracaoExplica}</p>
                    <table>
                      <thead>
                        <tr>
                          <th>{S.precisao.confiancaMedia}</th>
                          <th>{S.precisao.taxaAcerto}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {board.calibration.map((b, i) => (
                          <tr key={i}>
                            <td>{Math.round(b.confidence * 100)}%</td>
                            <td>{Math.round(b.hit_rate * 100)}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                )}
              </div>
            </>
          );
        }}
      </Async>
    </section>
  );
}
