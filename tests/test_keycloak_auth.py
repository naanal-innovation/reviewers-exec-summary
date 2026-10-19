import os
import time
import unittest
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

os.environ.update(
    KEYCLOAK_URL="https://kc.test",
    KEYCLOAK_REALM="master",
    KEYCLOAK_CLIENT_IDS="naanal,gollab-services",
    KEYCLOAK_SERVICE_CLIENT_ID="gollab-services",
)
os.environ.pop("AUTH_MODE", None)

try:
    import keycloak_auth
except ModuleNotFoundError:
    from app import keycloak_auth

ISSUER = "https://kc.test/realms/master"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(sub="user-1", iss=ISSUER, azp="naanal", exp_in=300, key=PRIVATE_KEY):
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "iss": iss, "azp": azp, "iat": now, "exp": now + exp_in},
        key,
        algorithm="RS256",
    )


def bearer(t=None):
    return {"Authorization": f"Bearer {t or token()}"}


def make_app():
    app = FastAPI()
    app.add_middleware(keycloak_auth.KeycloakAuthMiddleware, public_paths=("/health",))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://app.test"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/work")
    def work(request: Request):
        claims = request.state.token_claims
        return {"sub": claims and claims["sub"]}

    @app.post("/owned")
    def owned(request: Request, body: dict):
        keycloak_auth.require_user(request, body.get("user_id"))
        keycloak_auth.require_keys(request, body.get("keys", []))
        return {"ok": True}

    @app.post("/internal")
    def internal(request: Request):
        keycloak_auth.require_service_account(request)
        return {"ok": True}

    return app


class KeycloakAuthMiddlewareTest(unittest.TestCase):
    def setUp(self):
        signing_key = mock.Mock(key=PRIVATE_KEY.public_key())
        self.patch("_jwks_client", mock.Mock(get_signing_key_from_jwt=lambda _t: signing_key))
        self.patch("REPORT_ONLY", False)
        self.client = TestClient(make_app())

    def patch(self, name, value):
        patcher = mock.patch.object(keycloak_auth, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_health_needs_no_token(self):
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_valid_user_token_is_served(self):
        res = self.client.post("/work", headers=bearer())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"sub": "user-1"})

    def test_service_account_token_is_served(self):
        res = self.client.post("/work", headers=bearer(token(sub="svc", azp="gollab-services")))
        self.assertEqual(res.status_code, 200)

    def test_missing_token_is_refused(self):
        res = self.client.post("/work")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.headers["www-authenticate"], "Bearer")

    def test_bad_tokens_are_refused(self):
        bad_tokens = {
            "expired": token(exp_in=-120),
            "wrong-issuer": token(iss="https://evil.test/realms/master"),
            "wrong-client": token(azp="some-other-client"),
            "wrong-signature": token(key=OTHER_KEY),
            "garbage": "not-a-jwt",
        }
        for case, bad in bad_tokens.items():
            with self.subTest(case):
                self.assertEqual(self.client.post("/work", headers=bearer(bad)).status_code, 401)

    def test_refusal_carries_cors_headers(self):
        res = self.client.post("/work", headers={"Origin": "https://app.test"})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.headers["access-control-allow-origin"], "https://app.test")

    def test_preflight_needs_no_token(self):
        res = self.client.options(
            "/work",
            headers={
                "Origin": "https://app.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        self.assertEqual(res.status_code, 200)

    def test_unconfigured_service_refuses(self):
        self.patch("_jwks_client", None)
        self.assertEqual(self.client.post("/work", headers=bearer()).status_code, 503)

    def test_jwks_outage_is_503(self):
        def unreachable(_t):
            raise jwt.PyJWKClientError("connection refused")

        self.patch("_jwks_client", mock.Mock(get_signing_key_from_jwt=unreachable))
        self.assertEqual(self.client.post("/work", headers=bearer()).status_code, 503)

    def owned(self, user_id="user-1", keys=(), t=None):
        return self.client.post("/owned", json={"user_id": user_id, "keys": list(keys)}, headers=bearer(t))

    def test_user_may_use_own_id_and_folders(self):
        keys = ["documents/user-1/sub1/rasterize/page1.webp", "excalidrawData/user-1/sub1/0"]
        self.assertEqual(self.owned(keys=keys).status_code, 200)

    def test_user_may_not_act_for_another_user(self):
        self.assertEqual(self.owned(user_id="user-2").status_code, 403)
        self.assertEqual(self.owned(user_id=None).status_code, 403)

    def test_user_may_not_name_keys_outside_own_folders(self):
        for key in [
            "documents/user-2/sub1/page1.webp",
            "documents/user-1",  # the folder itself, not inside it
            "documents/user-10/x.webp",
            "temp/cropped_regions/user-1/x.png",
            "/etc/passwd",
            None,
        ]:
            with self.subTest(key):
                self.assertEqual(self.owned(keys=[key]).status_code, 403)

    def test_service_account_may_act_for_any_user(self):
        svc = token(sub="svc", azp="gollab-services")
        res = self.owned(user_id="user-2", keys=["documents/user-2/a.webp", "temp/x"], t=svc)
        self.assertEqual(res.status_code, 200)

    def test_internal_endpoints_are_for_the_service_account_only(self):
        self.assertEqual(self.client.post("/internal", headers=bearer()).status_code, 403)
        svc = token(sub="svc", azp="gollab-services")
        self.assertEqual(self.client.post("/internal", headers=bearer(svc)).status_code, 200)

    def test_report_mode_serves_unauthorized_access(self):
        self.patch("REPORT_ONLY", True)
        self.assertEqual(self.owned(user_id="user-2").status_code, 200)
        res = self.client.post("/owned", json={"user_id": "user-2"})
        self.assertEqual(res.status_code, 200)

    def test_report_mode_serves_but_does_not_authenticate(self):
        self.patch("REPORT_ONLY", True)
        res = self.client.post("/work")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"sub": None})


if __name__ == "__main__":
    unittest.main()
