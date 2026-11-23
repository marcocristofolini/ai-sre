import { useState } from "react";
import { api, ApiError, type Evidence, type IncidentDetail } from "../api";
import { Async, ConfidenceBadge, formatDate } from "../components";
import { S } from "../strings.pt";
import { useApi } from "../useApi";

export function IncidentList() {
  const state = useApi(api.incidents, [], 10_000);
  return (
    <section>
      <header className="page-head">
        <h1>{S.incidentes.titulo}</h1>
        <button onClick={state.reload}>{S.incidentes.atualizar}</button>
      </header>
      <Async state={state}>
        {(items) =>
          items.length === 0 ? (
            <p className="muted">{S.incidentes.vazio}</p>
          ) : (
            <ul className="list">
              {items.map((inc) => (
                <li key={inc.id} className="card row">
                  <a href={`#/incidentes/${inc.id}`}>
                    #{inc.id} {inc.alert_name}
                  </a>
                  <span className={`status status-${inc.status}`}>{S.incidentes.status[inc.status] ?? inc.status}</span>
                  {inc.rca && (inc.rca.unknown ? <span className="badge badge-baixa">{S.incidentes.naoSeiResumo}</span> : <ConfidenceBadge confidence={inc.rca.confidence} />)}
                  <span className="muted">{formatDate(inc.fired_at)}</span>
                </li>
              ))}
            </ul>
          )
        }
      </Async>
    </section>
  );
}

function EvidenceList({ items }: { items: Evidence[] }) {
  const [open, setOpen] = useState<number | null>(null);
  if (items.length === 0) return <p className="muted">{S.incidente.semEvidencias}</p>;
  return (
    <ul className="evidence">
      {items.map((ev, i) => (
        <li key={`${ev.ref}-${i}`}>
          <button
            className="ref"
            aria-expanded={open === i}
            onClick={() => setOpen(open === i ? null : i)}
          >
            [{S.incidente.tipoEvidencia[ev.kind] ?? ev.kind}] {ev.ref}
          </button>
          {open === i && <p className="evidence-summary">{ev.summary}</p>}
        </li>
      ))}
    </ul>
  );
}

function Approve({ inc, onDone }: { inc: IncidentDetail; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rca = inc.rca!;
  const command = rca.suggested_command!;
  // O botão só existe para RCA conhecido, com comando, e que o servidor marcou como aprovável
  // (comando exibível por inteiro). O comando aprovado é sempre o do RCA salvo no servidor.
  const canApprove = inc.approvable && !rca.unknown && inc.approvals.length === 0;

  async function approve() {
    setBusy(true);
    setError(null);
    try {
      await api.approve(inc.id);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError && err.detail ? err.detail : S.incidente.erroAprovar);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>{S.incidente.comando}</h2>
      <pre className="command">{command}</pre>
      <p className="warning" role="note">{S.incidente.comandoGeradoPorIa}</p>
      {!inc.command_read_only && <p className="warning">{S.incidente.comandoForaListaLeitura}</p>}
      <p className="muted">{S.incidente.comandoNaoExecutado}</p>
      {!inc.approvable && <p className="muted">{S.incidente.comandoNaoExibivel}</p>}
      {inc.approvals.length > 0 && (
        <>
          <p className="ok">{S.incidente.nadaExecutado}</p>
          <ul>
            {inc.approvals.map((a) => (
              <li key={a.user_id}>{S.incidente.aprovadoPor(a.user_id)}</li>
            ))}
          </ul>
        </>
      )}
      {canApprove && (
        <button className="primary" onClick={approve} disabled={busy}>
          {busy ? S.incidente.aprovando : S.incidente.aprovar}
        </button>
      )}
      {error && <p role="alert" className="error">{error}</p>}
    </div>
  );
}

export function IncidentView({ id }: { id: number }) {
  const state = useApi(() => api.incident(id), [id], 5_000);
  return (
    <section>
      <a href="#/incidentes">{S.incidente.voltar}</a>
      <Async state={state}>
        {(inc) => {
          const rca = inc.rca;
          return (
            <>
              <header className="page-head">
                <h1>{S.incidente.titulo(inc.id)}</h1>
                <span className={`status status-${inc.status}`}>{S.incidentes.status[inc.status] ?? inc.status}</span>
                {rca && <ConfidenceBadge confidence={rca.confidence} />}
              </header>
              <div className="card">
                <p className="muted">
                  {S.incidente.alerta}: <strong>{inc.alert_name}</strong> · {S.incidente.disparadoEm} {formatDate(inc.fired_at)}
                </p>
                {Object.keys(inc.labels).length > 0 && (
                  <p className="muted">
                    {S.incidente.rotulos}: {Object.entries(inc.labels).map(([k, v]) => `${k}=${v}`).join(", ")}
                  </p>
                )}
              </div>
              {inc.status === "failed" && (
                <div className="card">
                  <h2>{S.incidente.falha}</h2>
                  <p>{inc.error}</p>
                </div>
              )}
              {inc.status !== "failed" && !rca && <p className="muted">{S.incidente.semRca}</p>}
              {rca && (
                <>
                  <div className="card">
                    {rca.unknown || rca.cause === null ? (
                      <>
                        <h2>{S.incidente.naoSei}</h2>
                        <p className="muted">{S.incidente.naoSeiExplicacao}</p>
                      </>
                    ) : (
                      <>
                        <h2>{S.incidente.causa}</h2>
                        <p className="cause">{rca.cause}</p>
                      </>
                    )}
                  </div>
                  <div className="card">
                    <h2>{rca.unknown ? S.incidente.evidenciasColetadas : S.incidente.evidencias}</h2>
                    <EvidenceList items={rca.evidence} />
                  </div>
                  {!rca.unknown && rca.suggested_command && <Approve inc={inc} onDone={state.reload} />}
                </>
              )}
            </>
          );
        }}
      </Async>
    </section>
  );
}
