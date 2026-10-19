"""Keycloak bearer-token authentication for every endpoint of this service.

Each request must carry `Authorization: Bearer <Keycloak access token>`
signed by KEYCLOAK_ISSUER's keys and issued to one of KEYCLOAK_CLIENT_IDS
(the token's `azp`): the Gollab app for user requests, the backend service
account for calls between services. Only the paths passed as public_paths
(health checks) and CORS preflights are served without one.

Endpoints that read or write a user's S3 objects also call require_user /
require_keys / require_owner: a user token may only name its own user id and
keys inside its own folders (Gollab stores each user's objects under
`<folder>/<sub>/...`); the service account may act for any user.

AUTH_MODE=report logs what would be refused but still serves it, for rolling
the callers out before enforcement. Anything else enforces.
"""

import logging
import os

import jwt
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

log = logging.getLogger(__name__)

KEYCLOAK_URL = (os.environ.get("KEYCLOAK_URL") or "").rstrip("/")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM") or ""
KEYCLOAK_ISSUER = os.environ.get("KEYCLOAK_ISSUER") or (
    f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}" if KEYCLOAK_URL and KEYCLOAK_REALM else ""
)
KEYCLOAK_JWKS_URL = os.environ.get("KEYCLOAK_JWKS_URL") or (
    f"{KEYCLOAK_ISSUER}/protocol/openid-connect/certs" if KEYCLOAK_ISSUER else ""
)
# Optional: only accept tokens issued to these clients (the token's `azp`).
KEYCLOAK_CLIENT_IDS = {
    c.strip() for c in (os.environ.get("KEYCLOAK_CLIENT_IDS") or "").split(",") if c.strip()
}
# The backend service account's client: its tokens may act for any user.
KEYCLOAK_SERVICE_CLIENT_ID = os.environ.get("KEYCLOAK_SERVICE_CLIENT_ID") or ""
REPORT_ONLY = (os.environ.get("AUTH_MODE") or "enforce").lower() == "report"

# Top-level S3 folders whose second segment is the owning user's id.
USER_PREFIXES = ("documents", "appData", "excalidrawData", "excalidrawLibrary", "annotations")

_jwks_client = jwt.PyJWKClient(KEYCLOAK_JWKS_URL, cache_keys=True, lifespan=3600) if KEYCLOAK_JWKS_URL else None

if not _jwks_client:
    log.error("KEYCLOAK_URL/KEYCLOAK_REALM are not set: every protected request will be refused")
if REPORT_ONLY:
    log.warning("AUTH_MODE=report: authentication failures are logged, not enforced")


def _decode(token):
    signing_key = _jwks_client.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=KEYCLOAK_ISSUER,
        leeway=30,
        # Keycloak access tokens carry `aud: account` (or none) for SPA clients,
        # so the client is checked through `azp` below instead.
        options={"verify_aud": False, "require": ["exp", "iat", "sub"]},
    )
    if KEYCLOAK_CLIENT_IDS and claims.get("azp") not in KEYCLOAK_CLIENT_IDS:
        raise jwt.InvalidTokenError(f"token issued to client {claims.get('azp')!r}")
    return claims


def check(header):
    """(status, message, claims) for an Authorization header; status is None when it is valid."""
    if not header.startswith("Bearer "):
        return 401, "Missing bearer token", None
    if not _jwks_client:
        return 503, "Authentication is not configured", None
    try:
        return None, None, _decode(header[len("Bearer "):].strip())
    except jwt.PyJWKClientError as e:
        log.error("Could not fetch Keycloak signing keys: %s", e)
        return 503, "Authentication is unavailable", None
    except jwt.InvalidTokenError as e:
        return 401, f"Invalid token: {e}", None


class KeycloakAuthMiddleware:
    """ASGI middleware; add it before CORSMiddleware so refusals still carry CORS headers.

    Validated claims are put on `request.state.token_claims` (None when unauthenticated).
    """

    def __init__(self, app, public_paths=("/health",)):
        self.app = app
        self.public_paths = frozenset(public_paths)

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] == "OPTIONS"
            or scope["path"] in self.public_paths
        ):
            await self.app(scope, receive, send)
            return

        header = ""
        for name, value in scope["headers"]:
            if name == b"authorization":
                header = value.decode("latin-1")
                break
        # Off the event loop: a JWKS cache miss is a blocking HTTP fetch.
        status, message, claims = await run_in_threadpool(check, header)

        if status is not None:
            if not REPORT_ONLY:
                log.warning("auth refused %s %s with %s: %s", scope["method"], scope["path"], status, message)
                response = JSONResponse(
                    {"detail": message}, status_code=status, headers={"WWW-Authenticate": "Bearer"}
                )
                await response(scope, receive, send)
                return
            log.warning("auth (report mode) would refuse %s %s with %s: %s",
                        scope["method"], scope["path"], status, message)

        scope.setdefault("state", {})["token_claims"] = claims
        await self.app(scope, receive, send)


# --- Per-user authorization ---------------------------------------------------
# Each require_* raises 403 (or only logs, in report mode) unless the request's
# token may touch the named user's data.

def _caller(request):
    """(sub, is_service_account) of the request's validated token; sub is None without one."""
    claims = getattr(request.state, "token_claims", None) or {}
    is_service = bool(KEYCLOAK_SERVICE_CLIENT_ID) and claims.get("azp") == KEYCLOAK_SERVICE_CLIENT_ID
    return claims.get("sub"), is_service


def _forbid(request, message):
    if REPORT_ONLY:
        log.warning("authz (report mode) would refuse %s %s with 403: %s",
                    request.method, request.url.path, message)
        return
    log.warning("authz refused %s %s with 403: %s", request.method, request.url.path, message)
    raise HTTPException(status_code=403, detail=message)


def owns_key(sub, key):
    """Whether an S3 key lies inside one of the user's own folders."""
    return bool(sub) and isinstance(key, str) and any(
        key.startswith(f"{prefix}/{sub}/") for prefix in USER_PREFIXES
    )


def require_user(request, user_id):
    """The request may only act for its own user (the service account for any)."""
    sub, is_service = _caller(request)
    if is_service:
        return
    if not sub or user_id != sub:
        _forbid(request, "Access denied to another user's data")


def require_keys(request, keys):
    """Every S3 key must be inside the caller's own folders (the service account: any)."""
    sub, is_service = _caller(request)
    if is_service:
        return
    denied = [k for k in keys if not owns_key(sub, k)]
    if denied:
        _forbid(request, f"Access denied to {len(denied)} key(s)")


def require_owner(request, job):
    """A stored job may only be read by the user it was started for."""
    require_user(request, (job or {}).get("user_id"))


def require_service_account(request):
    """Only the backend service account (internal and debugging endpoints)."""
    if not _caller(request)[1]:
        _forbid(request, "Only available to backend services")
