# Imagem única: API FastAPI + dashboard React compilado. Nenhum segredo é embutido na imagem;
# toda configuração entra por variáveis AISRE_* em tempo de execução.
# Build (a partir da raiz do repositório): docker build -t aisre:0.1.0 .

# Digests: as imagens base abaixo usam só tag (não foi possível resolver digests sem rede; nenhum foi inventado).
# Para fixar por digest em um ambiente com rede:
#   docker buildx imagetools inspect node:22-slim        # copie "Digest: sha256:..."
#   docker buildx imagetools inspect python:3.12-slim
# e troque por `FROM node:22-slim@sha256:<digest> AS web` / `FROM python:3.12-slim@sha256:<digest> AS runtime`.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install .
COPY --from=web /web/dist /app/web/dist
ENV AISRE_WEB_DIST_DIR=/app/web/dist
RUN groupadd --system --gid 10001 aisre \
 && useradd --system --uid 10001 --gid aisre --no-create-home --shell /usr/sbin/nologin aisre
USER 10001:10001
EXPOSE 8000
# --proxy-headers: o IP do cliente vem de X-Forwarded-For SOMENTE quando a conexão chega de um IP em
# FORWARDED_ALLOW_IPS (lido pelo uvicorn do ambiente; padrão restritivo abaixo, o chart o define).
ENV FORWARDED_ALLOW_IPS=127.0.0.1
CMD ["uvicorn", "aisre.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
