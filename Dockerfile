# =============================================================================
# financial-agent
#
# Dos etapas y una sola imagen final (F7.2):
#
#   1. `frontend` compila React con Node y produce app/dist.
#   2. La imagen de Python copia ese dist y sirve la API con uvicorn.
#
# Node no llega a la imagen final. Eso importa: la etapa de compilacion se lleva
# `node_modules` entera —cientos de megas y todo un interprete— y en produccion
# no hace falta nada de eso, porque el frontend son tres ficheros estaticos que
# sirve FastAPI (F3.7).
#
# La imagen final sigue siendo UNA para los cuatro servicios (api, ingestor,
# scheduler, bot): el codigo es el mismo y lo unico que cambia es el `command`
# de cada uno en docker-compose.yml.
# =============================================================================

# -----------------------------------------------------------------------------
# Etapa 1: el frontend
# -----------------------------------------------------------------------------
FROM node:22-slim AS frontend

WORKDIR /build

# El manifiesto primero y en su propia capa: cambiar un componente de React no
# tiene por que reinstalar las dependencias.
COPY app/package.json app/package-lock.json ./
# `npm ci` y no `npm install`: instala exactamente lo del lock, sin actualizar
# nada por su cuenta. Una imagen que se construye dos veces tiene que dar lo
# mismo las dos veces.
RUN npm ci

COPY app/ ./
# `npm run build` pasa el typecheck antes de empaquetar, asi que un error de
# tipos rompe la construccion de la imagen en lugar de llegar al navegador.
RUN npm run build

# -----------------------------------------------------------------------------
# Etapa 2: la aplicacion
# -----------------------------------------------------------------------------
#
# Python 3.12 en lugar de la ultima version: es la que tiene rueda precompilada
# para todas las dependencias, asi que la imagen se construye sin compilador.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Las dependencias en su propia capa: cambiar codigo no reinstala paquetes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Usuario sin privilegios, creado antes de copiar el codigo para que Claude Code
# se instale en su home y en una capa que no se rehace con cada cambio.
RUN useradd --create-home --uid 10001 bot

# Claude Code, para los perfiles con `llm_provider='anthropic'` (F9.35): el
# modelo se llama con el comando `claude` y la suscripcion, no con la API. Va en
# la imagen y no en un servicio aparte porque el ciclo lo lanzan dos procesos
# -el planificador y la API, al pulsar «Lanzar»- y los dos usan esta imagen.
#
# La version va fijada, y el autoactualizador apagado: un CLI que se actualiza
# solo cambia sus flags debajo del experimento, y los flags son los que separan
# 186 tokens de entrada de 8.389 (ver src/claude_cli.py). curl solo hace falta
# para el instalador.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
USER bot
RUN curl -fsSL https://claude.ai/install.sh | bash -s 2.1.283
ENV PATH="/home/bot/.local/bin:${PATH}" \
    DISABLE_AUTOUPDATER=1
USER root

COPY . .

# El build de React, en la ruta exacta donde lo busca `APP_DIST` de
# api/deps.py. Si esto cambia de sitio, la API sirve la pagina de "falta el
# frontend" y cuesta un rato averiguar por que.
COPY --from=frontend /build/dist ./app/dist

# El directorio de datos se crea aqui con el dueno correcto para que el
# volumen sea escribible sin ejecutar como root.
RUN mkdir -p /app/data \
 && chown -R bot:bot /app
USER bot

# Dentro del contenedor la base vive siempre en el volumen, independientemente
# de lo que diga el .env del anfitrion.
ENV DB_PATH=/app/data/trading.db

EXPOSE 8000

# Por defecto, el diagnostico: `docker run` sin argumentos no opera nada.
CMD ["python", "run.py", "check"]
