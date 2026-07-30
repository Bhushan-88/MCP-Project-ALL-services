from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import psycopg2
import os

app = FastAPI(title="User Service", version="1.0.0")


def get_conn():
    host = os.getenv("POSTGRES_HOST")
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")

    if not host or not user or not password:
        raise RuntimeError("Database configuration is incomplete. Set POSTGRES_HOST, POSTGRES_USER, and POSTGRES_PASSWORD.")

    return psycopg2.connect(
        host=host,
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        database=os.getenv("POSTGRES_DB", "postgres"),
        user=user,
        password=password,
        sslmode=os.getenv("POSTGRES_SSLMODE", "require"),
    )


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "healthy", "service": "user-service"}


@app.get("/users")
def list_users():
    try:
        conn = get_conn()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    cur = conn.cursor()
    cur.execute("SELECT id, username, email, role, created_at FROM users ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    users = [
        {
            "id": str(r[0]),
            "username": r[1],
            "email": r[2],
            "role": r[3],
            "created_at": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]
    return {"users": users, "total": len(users)}


@app.get("/users/{user_id}")
def get_user(user_id: str):
    try:
        conn = get_conn()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    cur = conn.cursor()
    cur.execute("SELECT id, username, email, role, created_at FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {"id": str(row[0]), "username": row[1], "email": row[2], "role": row[3], "created_at": row[4].isoformat() if row[4] else None}


@app.put("/users/{user_id}")
def update_user(user_id: str, update: UserUpdate):
    try:
        conn = get_conn()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    cur = conn.cursor()
    if update.email:
        cur.execute("UPDATE users SET email = %s WHERE id = %s", (update.email, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"message": "User updated"}


@app.delete("/users/{user_id}")
def delete_user(user_id: str):
    try:
        conn = get_conn()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"message": "User deleted"}
