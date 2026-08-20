"""FastMCP demo server (SQLite-backed) for the SPIFFE/SPIRE lab.

Exposes a streamable-HTTP MCP endpoint at /mcp. The Envoy server sidecar
terminates mTLS in front of it, so transport authentication (workload identity)
is handled by SPIFFE. Optionally, this server also validates the Microsoft Entra
user access token forwarded in the Authorization header (the "token over HTTP
header" / OBO context in the diagram) when REQUIRE_ENTRA_TOKEN=true.
"""
import os
import sqlite3

import jwt
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from fastmcp import FastMCP

DB_PATH = os.environ.get("DB_PATH", "/data/demo.db")
REQUIRE_ENTRA = os.environ.get("REQUIRE_ENTRA_TOKEN", "false").lower() == "true"
TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "")
CLIENT_ID = os.environ.get("ENTRA_CLIENT_ID", "")

mcp = FastMCP("spiffe-demo-mcp")


# ----------------------------------------------------------------- sample data
def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS employees "
        "(id INTEGER PRIMARY KEY, name TEXT, role TEXT, department TEXT)"
    )
    if con.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0:
        con.executemany(
            "INSERT INTO employees (name, role, department) VALUES (?, ?, ?)",
            [
                ("Ada Lovelace", "Principal Engineer", "Platform"),
                ("Alan Turing", "Security Researcher", "Security"),
                ("Grace Hopper", "Engineering Manager", "Platform"),
            ],
        )
    con.commit()
    con.close()


def _query(sql: str, args: tuple = ()) -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(sql, args).fetchall()]
    con.close()
    return rows


# ----------------------------------------------------------------------- tools
@mcp.tool
def list_employees() -> list[dict]:
    """Return all employees in the demo directory."""
    return _query("SELECT id, name, role, department FROM employees ORDER BY id")


@mcp.tool
def find_employees(department: str) -> list[dict]:
    """Return employees in a given department."""
    return _query(
        "SELECT id, name, role, department FROM employees WHERE department = ?",
        (department,),
    )


# --------------------------------------------------- optional Entra token check
class EntraAuthMiddleware(BaseHTTPMiddleware):
    """Validate the forwarded Entra access token against the tenant JWKS."""

    def __init__(self, app):
        super().__init__(app)
        self._jwks = PyJWKClient(
            f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"
        )

    async def dispatch(self, request, call_next):
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)
        token = auth.split(" ", 1)[1]
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=CLIENT_ID,
                issuer=f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
            )
        except Exception as exc:  # noqa: BLE001 - surface auth failure to caller
            return JSONResponse({"error": f"invalid token: {exc}"}, status_code=401)
        return await call_next(request)


if __name__ == "__main__":
    import uvicorn

    init_db()
    app = mcp.http_app()  # Starlette ASGI app serving MCP at /mcp
    if REQUIRE_ENTRA:
        if not (TENANT_ID and CLIENT_ID):
            raise SystemExit("REQUIRE_ENTRA_TOKEN=true but ENTRA_TENANT_ID/CLIENT_ID unset")
        app.add_middleware(EntraAuthMiddleware)
        print("[mcp] Entra token validation ENABLED")
    else:
        print("[mcp] Entra token validation disabled (transport secured by SPIFFE mTLS)")

    # Bind loopback only: the MCP app is reachable exclusively through the Envoy
    # sidecar sharing this container's network namespace (mTLS on :9000).
    uvicorn.run(app, host="127.0.0.1", port=8000)
