from typing import Literal

from pydantic import BaseModel, Field, model_validator

from aisre import strings_pt as t


class Evidence(BaseModel):
    kind: Literal["metric", "log", "commit", "k8s"]
    ref: str
    summary: str


class RCA(BaseModel):
    cause: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    suggested_command: str | None = None
    unknown: bool = False
    runbooks: list[str] = Field(default_factory=list)  # nomes dos runbooks oferecidos ao agente (contexto, não evidência)

    @model_validator(mode="after")
    def _check_consistency(self) -> "RCA":
        if self.unknown and self.cause is not None:
            raise ValueError(t.ERRO_DESCONHECIDO_COM_CAUSA)
        if self.cause is not None and not self.evidence:
            raise ValueError(t.ERRO_CAUSA_SEM_EVIDENCIA)
        if self.cause is None and not self.unknown:
            raise ValueError(t.ERRO_SEM_CAUSA_NAO_DESCONHECIDO)
        return self
