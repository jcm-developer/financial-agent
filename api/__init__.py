"""API REST del agente (F3).

    uvicorn api.main:app --host 127.0.0.1 --port 8000
    python run.py api

Reparto de responsabilidades dentro del paquete:

    deps.py     configuracion, dependencias de base de datos, resolucion de perfil
    guard.py    la conexion de escritura acotada a las tablas de configuracion
    queries.py  las lecturas paginadas
    models.py   los modelos de request y response (y por tanto el OpenAPI)
    runner.py   lanzar `run.py cycle` como subproceso
    routes/     los endpoints
"""
