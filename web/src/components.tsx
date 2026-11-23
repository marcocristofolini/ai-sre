import type { ReactNode } from "react";
import { S } from "./strings.pt";
import type { Loaded } from "./useApi";

export function confidenceLevel(c: number): "alta" | "media" | "baixa" {
  return c >= 0.75 ? "alta" : c >= 0.5 ? "media" : "baixa";
}

export function ConfidenceBadge({ confidence }: { confidence: number }) {
  const level = confidenceLevel(confidence);
  return (
    <span className={`badge badge-${level}`} title={S.incidente.nivel[level]}>
      {S.incidente.confianca(Math.round(confidence * 100))}
    </span>
  );
}

export function formatScore(n: number): string {
  return n.toFixed(2).replace(".", ",");
}

export function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("pt-BR");
}

/** Estados de carregamento/erro comuns; o conteúdo só renderiza com dados. */
export function Async<T>({ state, children }: { state: Loaded<T>; children: (data: T) => ReactNode }) {
  if (state.data !== null) return <>{children(state.data)}</>;
  if (state.error) {
    return (
      <p role="alert" className="muted">
        {S.app.erroGenerico}{" "}
        <button onClick={state.reload}>{S.app.tentarNovamente}</button>
      </p>
    );
  }
  return <p className="muted">{S.app.carregando}</p>;
}
