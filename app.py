import base64
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException


ROOT = Path(__file__).resolve().parent
STATUSES = ("Applied", "Interview", "Offer", "Rejected")
FIELDS = {"company": 160, "role": 160, "link": 2048, "notes": 10000}
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
logger = logging.getLogger(__name__)


class ServiceError(Exception):
    def __init__(self, message, status=400):
        self.message = message
        self.status = status


def _utcnow():
    return datetime.now(timezone.utc)


def _json_body():
    if not request.is_json:
        raise ServiceError("Send a JSON request body.", 415)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ServiceError("The request body must be a JSON object.")
    return body


def _secret_key():
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        if os.environ.get("VERCEL") == "1" or os.environ.get("FLASK_ENV") == "production":
            raise RuntimeError("Set a strong SECRET_KEY before deploying.")
        secret = base64.urlsafe_b64encode(os.urandom(48)).decode("ascii")
        logger.warning("Using an ephemeral development SECRET_KEY; set SECRET_KEY for stable local sessions.")
    if len(secret) < 32:
        raise RuntimeError("SECRET_KEY must be at least 32 characters.")
    return secret


def _supabase_settings():
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not url or not key:
        raise ServiceError("Authentication and storage are not configured yet.", 503)
    parsed = urlsplit(url)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ServiceError("The Supabase URL is invalid.", 503)
    if parsed.scheme != "https" and not local_http:
        raise ServiceError("The Supabase URL must use HTTPS.", 503)
    return url, key


def _supabase(method, path, *, token=None, body=None, params=None, auth=False):
    url, key = _supabase_settings()
    headers = {"apikey": key, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    if method in {"POST", "PATCH", "DELETE"} and path.startswith("/rest/v1/"):
        headers["Prefer"] = "return=representation"
    if auth:
        headers["X-Client-Info"] = "pipeline-job-tracker"
    try:
        response = requests.request(
            method,
            f"{url}{path}",
            headers=headers,
            json=body,
            params=params,
            timeout=(3.05, 10),
            allow_redirects=False,
        )
    except requests.RequestException:
        logger.exception("Supabase request failed")
        raise ServiceError("The data service is temporarily unavailable. Please try again.", 503)
    try:
        data = response.json() if response.content else None
    except ValueError:
        data = None
    return response.status_code, data


def _cookie_session():
    token = request.cookies.get("pipeline_session")
    if not token:
        return None
    try:
        data = request.app_fernet.decrypt(token.encode("ascii"), ttl=60 * 60 * 24 * 30)
        session = json.loads(data)
        if not isinstance(session, dict) or not session.get("access_token") or not session.get("refresh_token"):
            return None
        return session
    except (InvalidToken, ValueError, UnicodeError, TypeError):
        g.clear_session_cookie = True
        return None


def _store_session(session):
    g.session_cookie = request.app_fernet.encrypt(json.dumps(session, separators=(",", ":")).encode()).decode("ascii")


def _user_session():
    if hasattr(g, "verified_user"):
        return g.verified_user
    session = _cookie_session()
    if not session:
        g.verified_user = None
        return None
    status, user = _supabase("GET", "/auth/v1/user", token=session["access_token"], auth=True)
    if status == 200 and isinstance(user, dict) and user.get("id") and (user.get("email_confirmed_at") or user.get("confirmed_at")):
        g.verified_user = (user, session)
        return g.verified_user
    if status not in (401, 403):
        raise ServiceError("Could not verify your session. Please try again.", 503)
    refresh_status, refreshed = _supabase(
        "POST",
        "/auth/v1/token",
        params={"grant_type": "refresh_token"},
        body={"refresh_token": session["refresh_token"]},
        auth=True,
    )
    if refresh_status == 200 and isinstance(refreshed, dict) and refreshed.get("access_token") and refreshed.get("refresh_token"):
        session.update(refreshed)
        _store_session(session)
        check_status, user = _supabase("GET", "/auth/v1/user", token=session["access_token"], auth=True)
        if (
            check_status == 200
            and isinstance(user, dict)
            and user.get("id")
            and (user.get("email_confirmed_at") or user.get("confirmed_at"))
        ):
            g.verified_user = (user, session)
            return g.verified_user
        if check_status not in (401, 403):
            raise ServiceError("Could not verify your session. Please try again.", 503)
    g.clear_session_cookie = True
    g.verified_user = None
    return None


def _require_user(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        if not _user_session():
            return jsonify(error="Please sign in to continue."), 401
        return handler(*args, **kwargs)

    return wrapped


def _check_origin():
    origin = request.headers.get("Origin")
    if not origin:
        raise ServiceError("This request could not be verified.", 403)
    configured = os.environ.get("APP_ORIGIN", "").strip().rstrip("/")
    allowed = {configured} if configured else set()
    if os.environ.get("VERCEL_URL"):
        allowed.add(f"https://{os.environ['VERCEL_URL'].strip().rstrip('/')}")
    if os.environ.get("VERCEL_BRANCH_URL"):
        allowed.add(f"https://{os.environ['VERCEL_BRANCH_URL'].strip().rstrip('/')}")
    if not allowed:
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip()
        allowed.add(f"{scheme}://{request.host}".rstrip("/"))
    if origin.rstrip("/") not in allowed:
        raise ServiceError("This request could not be verified.", 403)


def _validate_application(body, *, partial=False):
    values = {}
    allowed = set(FIELDS) | {"status"}
    if set(body) - allowed:
        raise ServiceError("The request contains unsupported fields.")
    for field in ("company", "role"):
        if not partial or field in body:
            value = body.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ServiceError(f"{field.capitalize()} is required.")
            value = value.strip()
            if len(value) > FIELDS[field]:
                raise ServiceError(f"{field.capitalize()} must be {FIELDS[field]} characters or fewer.")
            values[field] = value
    for field in ("link", "notes"):
        if field in body or not partial:
            value = body.get(field, "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise ServiceError(f"{field.capitalize()} must be text.")
            value = value.strip()
            if len(value) > FIELDS[field]:
                raise ServiceError(f"{field.capitalize()} must be {FIELDS[field]} characters or fewer.")
            if field == "link" and value:
                if len(value) > FIELDS[field] or not value.startswith(("https://", "http://")):
                    raise ServiceError("The link must start with https:// or http://.")
            values[field] = value
    if "status" in body:
        if body["status"] not in STATUSES:
            raise ServiceError("Choose a valid application status.")
        values["status"] = body["status"]
    elif not partial:
        values["status"] = "Applied"
    return values


def _public_origin():
    if os.environ.get("VERCEL_ENV") == "preview" and os.environ.get("VERCEL_URL"):
        return f"https://{os.environ['VERCEL_URL'].strip().rstrip('/')}"
    configured = os.environ.get("APP_ORIGIN", "").strip().rstrip("/")
    if configured:
        return configured
    if os.environ.get("VERCEL_URL"):
        return f"https://{os.environ['VERCEL_URL'].strip().rstrip('/')}"
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip()
    return f"{scheme}://{request.host}".rstrip("/")


def _fetch_apps(user_id):
    status, rows = _supabase(
        "GET",
        "/rest/v1/applications",
        token=_user_session()[1]["access_token"],
        params={
            "select": "id,company,role,link,notes,status,created_at,updated_at",
            "user_id": f"eq.{user_id}",
            "order": "created_at.desc",
        },
    )
    if status != 200 or not isinstance(rows, list):
        logger.error("Application query failed with status %s", status)
        raise ServiceError("Could not load applications. Check your database setup and try again.", 503)
    return rows


def create_app():
    app = Flask(__name__, static_folder=None)
    app.config.update(
        SECRET_KEY=_secret_key(),
        MAX_CONTENT_LENGTH=64 * 1024,
        JSON_SORT_KEYS=False,
    )
    digest = hashlib.sha256(app.config["SECRET_KEY"].encode("utf-8")).digest()
    app.request_fernet = Fernet(base64.urlsafe_b64encode(digest))

    @app.before_request
    def set_request_fernet():
        request.app_fernet = app.request_fernet
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.path.startswith("/api/"):
            _check_origin()

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        if request.path.startswith("/api/") or request.path == "/":
            response.headers["Cache-Control"] = "no-store"
        forwarded_proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip()
        secure = os.environ.get("VERCEL") == "1" or forwarded_proto == "https"
        if secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if getattr(g, "clear_session_cookie", False):
            response.delete_cookie("pipeline_session", path="/", secure=secure, httponly=True, samesite="Lax")
        elif getattr(g, "session_cookie", None):
            response.set_cookie(
                "pipeline_session",
                g.session_cookie,
                max_age=60 * 60 * 24 * 30,
                secure=secure,
                httponly=True,
                samesite="Lax",
                path="/",
            )
        return response

    @app.errorhandler(ServiceError)
    def service_error(error):
        return jsonify(error=error.message), error.status

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify(error="The request is too large."), 413

    @app.errorhandler(Exception)
    def unexpected_error(error):
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        logger.exception("Unhandled request error", exc_info=error)
        return jsonify(error="Something went wrong. Please try again."), 500

    @app.get("/")
    def index():
        return send_from_directory(ROOT / "templates", "index.html")

    @app.get("/static/<path:filename>")
    def static_files(filename):
        return send_from_directory(ROOT / "static", filename, max_age=0)

    @app.get("/healthz")
    def healthz():
        configured = bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_ANON_KEY"))
        return jsonify(status="ok", storage_configured=configured), 200

    @app.get("/api/config")
    def client_config():
        return jsonify(configured=bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_ANON_KEY")))

    @app.post("/api/auth/signup")
    def signup():
        body = _json_body()
        email = body.get("email", "")
        password = body.get("password", "")
        if not isinstance(email, str) or not EMAIL_RE.fullmatch(email.strip()):
            raise ServiceError("Enter a valid email address.")
        if not isinstance(password, str) or len(password) < 12 or len(password) > 128:
            raise ServiceError("Use a password between 12 and 128 characters.")
        status, result = _supabase(
            "POST",
            "/auth/v1/signup",
            body={"email": email.strip().lower(), "password": password},
            params={"redirect_to": _public_origin()},
            auth=True,
        )
        if status not in (200, 201) or not isinstance(result, dict):
            if status == 429:
                raise ServiceError("Too many attempts. Please wait a moment and try again.", 429)
            raise ServiceError("We could not create your account. Check the email and try again.", 400)
        session = result.get("session") or result
        user = result.get("user") or session.get("user") or {}
        if session.get("access_token") and session.get("refresh_token") and (
            user.get("email_confirmed_at") or user.get("confirmed_at")
        ):
            _store_session(session)
            return jsonify(verified=True), 201
        return jsonify(verified=False, message="Check your email for a verification link before signing in."), 202

    @app.post("/api/auth/login")
    def login():
        body = _json_body()
        email, password = body.get("email"), body.get("password")
        if not isinstance(email, str) or not EMAIL_RE.fullmatch(email.strip()):
            raise ServiceError("Enter a valid email address.")
        if not isinstance(password, str) or not password or len(password) > 128:
            raise ServiceError("Enter your password.")
        status, result = _supabase(
            "POST",
            "/auth/v1/token",
            params={"grant_type": "password"},
            body={"email": email.strip().lower(), "password": password},
            auth=True,
        )
        if (
            status != 200
            or not isinstance(result, dict)
            or not result.get("access_token")
            or not result.get("refresh_token")
            or not isinstance(result.get("user"), dict)
            or not (result["user"].get("email_confirmed_at") or result["user"].get("confirmed_at"))
        ):
            if status == 429:
                raise ServiceError("Too many attempts. Please wait a moment and try again.", 429)
            raise ServiceError("Email, password, or verification status is incorrect.", 401)
        _store_session(result)
        return jsonify(ok=True)

    @app.post("/api/auth/recovery")
    def recovery():
        body = _json_body()
        email = body.get("email", "")
        if not isinstance(email, str) or not EMAIL_RE.fullmatch(email.strip()):
            raise ServiceError("Enter a valid email address.")
        origin = _public_origin()
        status, _result = _supabase(
            "POST",
            "/auth/v1/recover",
            body={"email": email.strip().lower()},
            params={"redirect_to": f"{origin}/?flow=recovery"},
            auth=True,
        )
        if status not in (200, 204):
            if status == 429:
                raise ServiceError("Too many attempts. Please wait a moment and try again.", 429)
            raise ServiceError("We could not send a reset email. Please try again later.", 400)
        return jsonify(message="If an account exists for that email, a reset link has been sent.")

    @app.post("/api/auth/resend")
    def resend_verification():
        body = _json_body()
        email = body.get("email", "")
        if not isinstance(email, str) or not EMAIL_RE.fullmatch(email.strip()):
            raise ServiceError("Enter a valid email address.")
        status, _result = _supabase(
            "POST",
            "/auth/v1/resend",
            body={"type": "signup", "email": email.strip().lower()},
            auth=True,
        )
        if status not in (200, 204):
            raise ServiceError("We could not send a verification email. Please try again later.", 400)
        return jsonify(message="If your account needs verification, a new link has been sent.")

    @app.post("/api/auth/verify")
    def verify_email():
        body = _json_body()
        token_hash, flow = body.get("token_hash"), body.get("type")
        if not isinstance(token_hash, str) or len(token_hash) > 2048 or flow not in {"signup", "email", "recovery"}:
            raise ServiceError("This verification link is invalid or expired.")
        status, result = _supabase(
            "POST",
            "/auth/v1/verify",
            body={"token_hash": token_hash, "type": flow},
            auth=True,
        )
        if status != 200 or not isinstance(result, dict):
            raise ServiceError("This verification link is invalid or expired.", 400)
        if result.get("access_token") and result.get("refresh_token"):
            _store_session(result)
        return jsonify(ok=True, recovery=flow == "recovery")

    @app.get("/api/auth/session")
    def auth_session():
        result = _user_session()
        if not result:
            return jsonify(user=None)
        user, _session = result
        return jsonify(user={"email": user.get("email", "")})

    @app.post("/api/auth/password")
    @_require_user
    def update_password():
        body = _json_body()
        password = body.get("password")
        if not isinstance(password, str) or len(password) < 12 or len(password) > 128:
            raise ServiceError("Use a password between 12 and 128 characters.")
        _user, session = _user_session()
        status, _result = _supabase(
            "PUT",
            "/auth/v1/user",
            token=session["access_token"],
            body={"password": password},
            auth=True,
        )
        if status != 200:
            raise ServiceError("Your password could not be updated. Please try again.", 400)
        return jsonify(ok=True)

    @app.post("/api/auth/logout")
    def logout():
        session = _cookie_session()
        if session:
            status, _result = _supabase("POST", "/auth/v1/logout", token=session["access_token"], auth=True)
            if status not in (200, 204, 401):
                raise ServiceError("Could not sign out. Please try again.", 503)
        g.clear_session_cookie = True
        return jsonify(ok=True)

    @app.get("/api/applications")
    @_require_user
    def list_applications():
        user, _session = _user_session()
        rows = _fetch_apps(user["id"])
        cutoff = _utcnow() - timedelta(days=7)
        for row in rows:
            try:
                updated = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
                row["stale"] = row["status"] == "Applied" and updated < cutoff
            except (KeyError, TypeError, ValueError):
                row["stale"] = False
        return jsonify(applications=rows)

    @app.post("/api/applications")
    @_require_user
    def create_application():
        values = _validate_application(_json_body())
        user, session = _user_session()
        values["user_id"] = user["id"]
        status, result = _supabase(
            "POST",
            "/rest/v1/applications",
            token=session["access_token"],
            body=values,
            params={"select": "id,company,role,link,notes,status,created_at,updated_at"},
        )
        if status not in (200, 201) or not isinstance(result, list) or not result:
            logger.error("Application insert failed with status %s", status)
            raise ServiceError("Could not save this application. Check your database setup and try again.", 503)
        return jsonify(application=result[0]), 201

    @app.patch("/api/applications/<app_id>")
    @_require_user
    def update_application(app_id):
        values = _validate_application(_json_body(), partial=True)
        if not values:
            raise ServiceError("There are no changes to save.")
        user, session = _user_session()
        status, result = _supabase(
            "PATCH",
            "/rest/v1/applications",
            token=session["access_token"],
            body=values,
            params={
                "id": f"eq.{app_id}",
                "user_id": f"eq.{user['id']}",
                "select": "id,company,role,link,notes,status,created_at,updated_at",
            },
        )
        if status not in (200, 204) or not isinstance(result, list):
            logger.error("Application update failed with status %s", status)
            raise ServiceError("Could not update this application.", 503)
        if not result:
            raise ServiceError("Application not found.", 404)
        return jsonify(application=result[0])

    @app.delete("/api/applications/<app_id>")
    @_require_user
    def delete_application(app_id):
        user, session = _user_session()
        status, result = _supabase(
            "DELETE",
            "/rest/v1/applications",
            token=session["access_token"],
            params={"id": f"eq.{app_id}", "user_id": f"eq.{user['id']}", "select": "id"},
        )
        if status not in (200, 204) or (isinstance(result, list) and not result):
            logger.error("Application delete failed with status %s", status)
            raise ServiceError("Could not delete this application.", 503)
        return jsonify(ok=True)

    @app.get("/api/applications/<app_id>/history")
    @_require_user
    def application_history(app_id):
        user, session = _user_session()
        status, result = _supabase(
            "GET",
            "/rest/v1/application_history",
            token=session["access_token"],
            params={
                "select": "id,status,changed_at",
                "application_id": f"eq.{app_id}",
                "user_id": f"eq.{user['id']}",
                "order": "changed_at.desc",
            },
        )
        if status != 200 or not isinstance(result, list):
            logger.error("Application history query failed with status %s", status)
            raise ServiceError("Could not load application history.", 503)
        return jsonify(history=result)

    @app.get("/api/stats")
    @_require_user
    def stats():
        user, _session = _user_session()
        rows = _fetch_apps(user["id"])
        counts = {status: sum(row.get("status") == status for row in rows) for status in STATUSES}
        total = len(rows)
        cutoff = _utcnow() - timedelta(days=7)
        stale = 0
        for row in rows:
            try:
                stale += row.get("status") == "Applied" and datetime.fromisoformat(
                    row["updated_at"].replace("Z", "+00:00")
                ) < cutoff
            except (KeyError, TypeError, ValueError):
                continue
        return jsonify(
            counts=counts,
            total=total,
            stale=stale,
            response_rate=round((total - counts["Applied"]) / total * 100) if total else 0,
            active=total - counts["Rejected"],
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
