"""Authenticated App Service banking API; Easy Auth is a required trust boundary."""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from banking import Bank, CUSTOMERS
from commerce import DomainError
from foundry import Foundry, FoundryError
from gateway import Grants, handle as gateway_rpc
from hosted_banking import HostedBanking
from settings import ROOT, load_config, read_environment, survey_url

LOG = logging.getLogger("banking.cloud")


GATEWAY_PATH = "/gateway/mcp"


def principal(headers, env, allowlist="CLOUD_ALLOWED_PRINCIPAL_IDS"):
    host = headers.get("host", "")
    if host.endswith(":443"):
        host = host[:-4]
    if host != urlparse(env["CLOUD_EXPECTED_ORIGIN"]).netloc:
        raise DomainError("Unrecognized application host.", 403)
    try:
        raw = base64.b64decode(headers.get("x-ms-client-principal", ""), validate=True)
        value = json.loads(raw)
        claims = {item["typ"]: item["val"] for item in value["claims"]}
        tenant = claims.get("http://schemas.microsoft.com/identity/claims/tenantid", claims.get("tid"))
        oid = claims.get("http://schemas.microsoft.com/identity/claims/objectidentifier", claims.get("oid"))
        allowed = {str(UUID(item.strip())) for item in env.get(allowlist, "").split(",") if item.strip()}
        if value["auth_typ"] != "aad" or tenant != env["CLOUD_AUTH_TENANT"] or str(UUID(oid)) not in allowed:
            raise ValueError()
        return str(UUID(oid))
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise DomainError("Sign in with an authorized banking presenter account.", 401) from exc


def csrf(env, owner):
    return hmac.new(env["CLOUD_CSRF_SECRET"].encode(), owner.encode(), hashlib.sha256).hexdigest()


def own_session(bank, session, owner):
    if not isinstance(session, str):
        raise DomainError("Start a banking session.", 401)
    with bank.db() as db:
        row = db.execute("SELECT owner FROM bank_owners WHERE session=?", (session,)).fetchone()
    if not row or row["owner"] != owner:
        raise DomainError("This banking session does not belong to your signed-in account.", 403)


@asynccontextmanager
async def lifespan(application):
    env = read_environment()
    for key in ("WEBSITE_SITE_NAME", "CLOUD_EXPECTED_ORIGIN", "CLOUD_AUTH_TENANT",
                "CLOUD_ALLOWED_PRINCIPAL_IDS", "CLOUD_CSRF_SECRET", "CLOUD_REVIEWER_IDS"):
        if not env.get(key):
            raise RuntimeError(f"Required authenticated cloud setting is missing: {key}")
    origin = urlparse(env["CLOUD_EXPECTED_ORIGIN"])
    if origin.scheme != "https" or not origin.netloc or origin.path not in ("", "/"):
        raise RuntimeError("The cloud application requires its canonical HTTPS origin.")
    application.state.env = env
    application.state.bank = Bank(Path(env.get("BANKING_DATABASE", "/home/data/banking.db")))
    with application.state.bank.db() as db:
        db.execute("CREATE TABLE IF NOT EXISTS bank_owners(session TEXT PRIMARY KEY,owner TEXT NOT NULL)")
    application.state.client = Foundry(load_config("banking"))
    governed = bool(env.get("BANKING_GATEWAY_TOOLBOX_MCP") and env.get("CLOUD_GATEWAY_PRINCIPAL_IDS"))
    gateways = {item.strip().lower() for item in env.get("CLOUD_GATEWAY_PRINCIPAL_IDS", "").split(",") if item}
    if gateways & {item.strip().lower() for item in env["CLOUD_ALLOWED_PRINCIPAL_IDS"].split(",")}:
        raise RuntimeError("The gateway identity must not also be a presenter identity.")
    application.state.grants = Grants()
    application.state.hosted = HostedBanking(
        application.state.bank, application.state.client, application.state.grants, governed=governed,
        isolation_secret=(env["CLOUD_CSRF_SECRET"] if env.get("BANKING_HOSTED_ISOLATION") == "delegated"
                          else None),
        version=env.get("BANKING_HOSTED_ORCHESTRATOR_VERSION"))
    application.state.active_calls = set()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def authenticate(request: Request, call_next):
    try:
        gateway = request.url.path == GATEWAY_PATH
        # The Toolbox identity is accepted only on the gateway route, never on presenter routes.
        request.state.owner = principal(request.headers, app.state.env,
                                        "CLOUD_GATEWAY_PRINCIPAL_IDS" if gateway else "CLOUD_ALLOWED_PRINCIPAL_IDS")
        response = await call_next(request)
    except DomainError as exc:
        response = JSONResponse({"error": str(exc)}, status_code=exc.status)
    response.headers.update({
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
        "Permissions-Policy": "microphone=(self), camera=()",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; connect-src 'self'; media-src 'self' blob:; worker-src 'self'; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'none'",
    })
    return response


@app.get("/")
async def home():
    return RedirectResponse("/banking")


@app.get("/api/bank/config")
async def configuration(request: Request):
    c, env = load_config("banking"), app.state.env
    reviewer = request.state.owner in env["CLOUD_REVIEWER_IDS"].split(",")
    return {"csrf_token": csrf(env, request.state.owner), "project": c["project_name"],
            "region": c["region"], "project_state": c["project_state"],
            "voice_version": c["voice_agent_version"], "cloud": True,
            "voice_profiles": {locale: {"version": profile["version"]}
                               for locale, profile in c["voice_profiles"].items()},
            "reviewer": reviewer, "survey": survey_url(),
            "customers": [{"id": key, "name": value["name"], "locale": value["locale"],
                           "demo_dob": value["dob"]} for key, value in CUSTOMERS.items()]}


@app.post(GATEWAY_PATH)
async def governed_gateway(request: Request):
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        return JSONResponse({"error": "JSON request content is required."}, status_code=415)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            return JSONResponse({"error": "Gateway requests are limited to 8192 bytes."}, status_code=413)
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        body = None
    try:
        status, value = await asyncio.to_thread(gateway_rpc, app.state.bank, app.state.grants, body)
    except sqlite3.Error:
        LOG.exception("Governed gateway storage failed.")
        return JSONResponse({"error": "Banking storage is unavailable."}, status_code=503)
    if value is None:
        return Response(status_code=status)
    if value.get("result", {}).get("isError"):
        LOG.warning("Governed gateway refused a tool call.")
    return JSONResponse(value, status_code=status)


@app.get(GATEWAY_PATH)
async def gateway_stream():
    return JSONResponse({"error": "Server-sent event streams are not offered."}, status_code=405)


@app.get("/{asset}")
async def asset_file(asset: str):
    files = {"banking": "banking.html", **{name: name for name in
             ("banking.js", "banking.css", "style.css", "voice.js", "pcm-worklet.js")}}
    if asset not in files:
        return JSONResponse({"error": "Not found."}, status_code=404)
    return FileResponse(ROOT / "web" / files[asset])


@app.post("/api/bank/{operation}")
async def banking_request(operation: str, request: Request):
    env, bank, owner = app.state.env, app.state.bank, request.state.owner
    try:
        if request.headers.get("origin") != env["CLOUD_EXPECTED_ORIGIN"]:
            raise DomainError("Same-origin banking requests are required.", 403)
        if not hmac.compare_digest(request.headers.get("x-baku-token", "").encode(), csrf(env, owner).encode()):
            raise DomainError("Refresh the banking page before continuing.", 403)
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise DomainError("JSON request content is required.", 415)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 8192:
                raise DomainError("Banking requests are limited to 8192 bytes.", 413)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise DomainError("A valid JSON object is required.") from exc
        if not isinstance(body, dict):
            raise DomainError("A JSON object is required.")
        if operation == "start":
            with bank.db() as db:
                count = db.execute("SELECT COUNT(*) FROM bank_owners WHERE owner=?", (owner,)).fetchone()[0]
            if count >= 100:
                raise DomainError("Presenter session limit reached. Reuse an existing session.", 429)
            value = bank.start(body.get("customer"), body.get("locale"))
            with bank.db() as db:
                db.execute("INSERT INTO bank_owners VALUES(?,?)", (value["session"], owner))
            return JSONResponse(value, status_code=201)
        session = body.get("session")
        own_session(bank, session, owner)
        if operation == "state":
            return bank.view(session)
        if operation == "action":
            return await asyncio.to_thread(app.state.hosted.action, session,
                                            body.get("operation"), body.get("payload"))
        if operation == "decision":
            if owner not in env["CLOUD_REVIEWER_IDS"].split(","):
                raise DomainError("Your signed-in account is not a credit reviewer.", 403)
            return bank.decide(session, body.get("decision"), body.get("acknowledged"), actor="human:" + owner)
        if operation == "audit":
            value = bank.audit()
            with bank.db() as db:
                owned = {row[0] for row in db.execute("SELECT session FROM bank_owners WHERE owner=?", (owner,))}
            value["events"] = [event for event in value["events"] if event["session"] in owned]
            return value
        raise DomainError("Unknown banking route.", 404)
    except DomainError as exc:
        LOG.warning("Banking request refused: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=exc.status)
    except FoundryError as exc:
        LOG.error("Hosted banking request failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=502)
    except sqlite3.Error:
        LOG.exception("Banking database request failed.")
        return JSONResponse({"error": "Banking storage is unavailable. No success is confirmed."}, status_code=503)


@app.websocket("/api/bank/voice")
async def voice(socket: WebSocket):
    from voice_bridge import bridge
    await bridge(socket, app.state)
