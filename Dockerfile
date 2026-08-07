# =============================================================================
# financial-bot
#
# Una sola imagen para los tres usos (dashboard, planificador, comandos
# puntuales): el codigo es el mismo y lo unico que cambia es el `command` de
# cada servicio en docker-compose.yml.
#
# Python 3.12 en lugar de la ultima version: es la que tiene rueda precompilada
# para todas las dependencias, asi que la imagen se construye sin compilador.
# =============================================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Las dependencias en su propia capa: cambiar codigo no reinstala paquetes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Usuario sin privilegios. El directorio de datos se crea aqui con el dueno
# correcto para que el volumen sea escribible sin ejecutar como root.
RUN useradd --create-home --uid 10001 bot \
 && mkdir -p /app/data \
 && chown -R bot:bot /app
USER bot

# Dentro del contenedor la base vive siempre en el volumen, independientemente
# de lo que diga el .env del anfitrion.
ENV DB_PATH=/app/data/trading.db

EXPOSE 8000

# Por defecto, el diagnostico: `docker run` sin argumentos no opera nada.
CMD ["python", "run.py", "check"]
