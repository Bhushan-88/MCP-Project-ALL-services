from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
import psycopg2
import os

app = FastAPI(title="Auth Service", version="1.0.0")

SECRET_KEY = os.getenv("JWT_SECRET", "change-me-in-production")
ALGORITHM  = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── DB connection ─────────────────────────────────────────────
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


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id         SERIAL PRIMARY KEY,
            username   VARCHAR(100) UNIQUE NOT NULL,
            email      VARCHAR(255) UNIQUE NOT NULL,
            password   VARCHAR(255) NOT NULL,
            role       VARCHAR(50)  NOT NULL DEFAULT 'user',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # Seed default admin if not exists
    cur.execute("SELECT id FROM users WHERE username = 'admin'")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (username, email, password, role) VALUES (%s, %s, %s, %s)",
            ("admin", "admin@mcp-platform.com", hash_password("admin123"), "admin")
        )
    conn.commit()
    cur.close()
    conn.close()


# ── Helpers ───────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return pwd_ctx.hash(password[:72])


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain[:72], hashed)


def create_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# Run on startup
try:
    init_db()
except Exception as e:
    print(f"DB init warning: {e}")


# ── Models ────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


# ── Routes ────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "healthy", "service": "auth-service"}


@app.post("/register")
def register(req: RegisterRequest):
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    try:
        conn = get_conn()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (username, email, password, role) VALUES (%s, %s, %s, %s)",
            (req.username, req.email, hash_password(req.password), "user")
        )
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=409, detail="Username or email already exists")
    finally:
        cur.close()
        conn.close()
    return {"message": "Account created successfully"}


@app.post("/login", response_model=TokenResponse)
def login(req: LoginRequest):
    try:
        conn = get_conn()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    cur = conn.cursor()
    cur.execute("SELECT password, role FROM users WHERE username = %s", (req.username,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row or not verify_password(req.password, row[0]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_token({"sub": req.username, "role": row[1]})
    return TokenResponse(access_token=token, role=row[1])


@app.post("/verify-header")
def verify_token(authorization: str):
    try:
        token = authorization.replace("Bearer ", "")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return {"valid": True, "user": payload.get("sub"), "role": payload.get("role")}
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


@app.post("/refresh")
def refresh_token(authorization: str):
    try:
        token = authorization.replace("Bearer ", "")
        # Decode without expiry check so expired tokens can still be refreshed
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"verify_exp": False})
        sub  = payload.get("sub")
        role = payload.get("role")
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid token")
        new_token = create_token({"sub": sub, "role": role})
        return {"access_token": new_token, "token_type": "bearer", "role": role}
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
