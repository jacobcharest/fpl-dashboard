from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import get_connection

app = FastAPI(title="FPL Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/seasons")
def list_seasons():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, label, backfilled FROM seasons ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
