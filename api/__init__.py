"""The agent's REST API (F3).

    uvicorn api.main:app --host 127.0.0.1 --port 8000
    python run.py api

How responsibilities are split inside the package:

    deps.py     configuration, database dependencies, profile resolution
    guard.py    the write connection, fenced to the configuration tables
    queries.py  the paginated reads
    models.py   the request and response models (and therefore the OpenAPI)
    runner.py   launching `run.py cycle` as a subprocess
    routes/     the endpoints
"""
