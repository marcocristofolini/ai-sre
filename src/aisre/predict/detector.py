"""Detector preditivo por workload (Isolation Forest).

Comportamento com entradas degeneradas (documentado, coberto por testes):
- `fit` descarta valores NaN/inf; série vazia ou com menos de `min_baseline_points`
  pontos finitos deixa o detector em "aprendendo" (nenhum modelo é mantido).
- `score` retorna 0.0 enquanto "aprendendo" e para ponto vazio ou não finito
  (sem sinal, nunca dispara); número de features diferente do ajuste levanta ValueError.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from sklearn.ensemble import IsolationForest

State = Literal["aprendendo", "ativo"]


def _as_matrix(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim == 0:
        return arr.reshape(1, 1)
    if arr.ndim == 1:
        return arr.reshape(-1, 1)
    return arr.reshape(arr.shape[0], -1)


class WorkloadDetector:
    def __init__(self, min_baseline_points: int = 500) -> None:
        if min_baseline_points < 1:
            raise ValueError("min_baseline_points deve ser >= 1")
        self._min = min_baseline_points
        self._model: IsolationForest | None = None
        self._features = 1

    @property
    def state(self) -> State:
        return "ativo" if self._model is not None else "aprendendo"

    def fit(self, series: np.ndarray) -> None:
        data = _as_matrix(series) if np.size(series) else np.empty((0, 1))
        data = data[np.isfinite(data).all(axis=1)]
        if data.shape[0] < self._min:
            self._model = None
            return
        model = IsolationForest(n_estimators=100, random_state=42)
        model.fit(data)
        self._model, self._features = model, data.shape[1]

    def score(self, point: np.ndarray) -> float:
        """Risco em [0, 1]; maior = mais anômalo."""
        if self._model is None:
            return 0.0
        arr = np.asarray(point, dtype=np.float64)
        if arr.size == 0 or not np.isfinite(arr).all():
            return 0.0
        row = arr.reshape(1, -1)
        if row.shape[1] != self._features:
            raise ValueError("número de features diferente do ajuste")
        # score_samples = -s(x) do artigo (s em (0,1], >0.5 tende a anomalia).
        risk = -float(self._model.score_samples(row)[0])
        return min(1.0, max(0.0, risk))
