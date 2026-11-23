{{- define "aisre.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "aisre.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "aisre.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "aisre.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "aisre.selectorLabels" -}}
app.kubernetes.io/name: {{ include "aisre.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: app
{{- end -}}

{{- define "aisre.postgres.selectorLabels" -}}
app.kubernetes.io/name: {{ include "aisre.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: postgres
{{- end -}}

{{- define "aisre.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "aisre.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "aisre.image" -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{- if eq $tag "latest" -}}{{- fail "image.tag não pode ser latest" -}}{{- end -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}

{{/* Nome do Secret da aplicação: existente ou o criado pelo chart (só com valores informados). */}}
{{- define "aisre.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else if .Values.secrets.create -}}
{{- printf "%s-secrets" (include "aisre.fullname" .) -}}
{{- else -}}
{{- fail "Informe secrets.existingSecret (recomendado) ou secrets.create=true com secrets.values.*; veja o README." -}}
{{- end -}}
{{- end -}}

{{- define "aisre.postgres.secretName" -}}
{{- if .Values.postgres.auth.existingSecret -}}
{{- .Values.postgres.auth.existingSecret -}}
{{- else -}}
{{- fail "Informe postgres.auth.existingSecret (Secret com a senha do Postgres) ou use postgres.enabled=false com postgres.external.*." -}}
{{- end -}}
{{- end -}}

{{/* Secret e chave com a URL do banco. */}}
{{- define "aisre.dbUrl.secretName" -}}
{{- if .Values.postgres.enabled -}}
{{- include "aisre.postgres.secretName" . -}}
{{- else if .Values.postgres.external.existingSecret -}}
{{- .Values.postgres.external.existingSecret -}}
{{- else if .Values.postgres.external.url -}}
{{- printf "%s-db" (include "aisre.fullname" .) -}}
{{- else -}}
{{- fail "Com postgres.enabled=false informe postgres.external.existingSecret ou postgres.external.url." -}}
{{- end -}}
{{- end -}}
