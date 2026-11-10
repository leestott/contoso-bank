"""Loopback-only demo server; Python 3.11+ and Azure CLI, no pip dependencies."""

import argparse
import json
import logging
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from commerce import CATALOG, DomainError, Store, integer
from banking import Bank, CUSTOMERS
from foundry import Foundry, FoundryError, ROOT
from settings import load_config, survey_url

LOG = logging.getLogger("baku")


def handler_for(store, foundry, token, bank=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            LOG.info(fmt, *args)

        def headers_common(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self'; "
                             "img-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                             "form-action 'none'; base-uri 'none'")

        def send_json(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.headers_common()
            self.end_headers()
            self.wfile.write(body)

        def allowed_host(self):
            return self.headers.get("Host") in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }

        def do_GET(self):
            try:
                self.get_response()
            except ValueError:
                LOG.error("Invalid local environment configuration")
                self.send_json({"error": "Invalid local .env configuration. Check setting names and syntax."}, 500)

        def get_response(self):
            if not self.allowed_host():
                return self.send_json({"error": "Loopback Host required."}, 403)
            path = urlparse(self.path).path
            if path == "/api/state":
                state = store.state()
                state.update({"csrf_token": token, "survey": survey_url(), "foundry": {
                    "project": foundry.config["project_name"],
                    "agent": foundry.config["agent_name"],
                    "version": foundry.config["agent_version"],
                    "model": foundry.config["model_deployment"],
                }})
                return self.send_json(state)
            if path == "/api/audit":
                return self.send_json({"events": store.state()["events"]})
            if path == "/api/bank/config" and bank is not None:
                config = load_config("banking")
                return self.send_json({
                    "csrf_token": token, "project": config["project_name"],
                    "region": config["region"], "project_state": config["project_state"],
                    "voice_version": config["voice_agent_version"],
                    "survey": survey_url(),
                    "customers": [{"id": key, "name": value["name"], "locale": value["locale"],
                                   "demo_dob": value["dob"]} for key, value in CUSTOMERS.items()],
                })
            assets = {"/": ("index.html", "text/html"),
                      "/app.js": ("app.js", "text/javascript"),
                      "/style.css": ("style.css", "text/css"),
                      "/banking": ("banking.html", "text/html"),
                      "/banking.js": ("banking.js", "text/javascript"),
                      "/voice.js": ("voice.js", "text/javascript"),
                      "/pcm-worklet.js": ("pcm-worklet.js", "text/javascript"),
                      "/banking.css": ("banking.css", "text/css")}
            if path not in assets:
                return self.send_json({"error": "Not found."}, 404)
            name, content_type = assets[path]
            body = (ROOT / "web" / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.headers_common()
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                if not self.allowed_host():
                    raise DomainError("Loopback Host required.", 403)
                origin = self.headers.get("Origin")
                if origin and origin not in {
                    f"http://127.0.0.1:{self.server.server_port}",
                    f"http://localhost:{self.server.server_port}",
                }:
                    raise DomainError("Cross-origin requests are not allowed.", 403)
                if not secrets.compare_digest(self.headers.get("X-Baku-Token", ""), token):
                    raise DomainError("Refresh the page to establish a local demo session.", 403)
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise DomainError("Content-Type must be application/json.", 415)
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise DomainError("Invalid Content-Length.") from exc
                if not 0 < length <= 8192:
                    raise DomainError("Request body must be from 1 to 8192 bytes.", 413)
                try:
                    body = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeError) as exc:
                    raise DomainError("Request body must be valid JSON.") from exc
                if not isinstance(body, dict):
                    raise DomainError("Request body must be a JSON object.")
                path = urlparse(self.path).path
                if path.startswith("/api/bank/") and bank is not None:
                    session = body.get("session")
                    if path == "/api/bank/start":
                        return self.send_json(bank.start(body.get("customer"), body.get("locale")), 201)
                    if path == "/api/bank/state":
                        return self.send_json(bank.view(session))
                    if path == "/api/bank/action":
                        return self.send_json(bank.action(
                            session, body.get("operation"), body.get("payload"),
                            actor="local-demo-operator"))
                    if path == "/api/bank/decision":
                        return self.send_json(bank.decide(
                            session, body.get("decision"), body.get("acknowledged")))
                    if path == "/api/bank/audit":
                        return self.send_json(bank.audit())
                    raise DomainError("Not found.", 404)
                if path == "/api/recommend":
                    quantity = integer(body.get("quantity"), "Quantity", 1, 100)
                    category = body.get("category")
                    catalog = [p for p in CATALOG if p["category"] == category]
                    if not catalog:
                        raise DomainError("Choose headsets, monitors or docks.")
                    request = body.get("request", "")
                    if not isinstance(request, str) or not 1 <= len(request.strip()) <= 1000:
                        raise DomainError("Enter a synthetic purchase request from 1 to 1000 characters.")
                    mode = body.get("mode")
                    if mode == "foundry":
                        product_id, reason, response_id = foundry.recommend(
                            request, catalog, quantity, store.state()["policy"]
                        )
                        source = f"Foundry: {foundry.config['agent_name']} v{foundry.config['agent_version']}"
                    elif mode == "rules":
                        policy = store.state()["policy"]
                        approved = [p for p in catalog if p["merchant"] in policy["merchants"]]
                        selected = min(approved or catalog, key=lambda p: p["price"])
                        product_id = selected["id"]
                        reason = "Rule-based demo: lowest-priced item from allowed merchants in this category."
                        if not approved:
                            reason = "Rule-based demo: no allowed merchant; cheapest item shown to demonstrate blocking."
                        response_id, source = None, "Rule-based demo"
                    else:
                        raise DomainError("Choose Foundry agent or explicit rule-based demo mode.")
                    order = store.create(product_id, quantity, reason, source, request, response_id)
                    return self.send_json({"order": order}, 201)
                if path == "/api/catalog-order":
                    order = store.create(body.get("product_id"), body.get("quantity"),
                                         "Manual catalog selection to exercise the policy gates.",
                                         "Demo operator")
                    return self.send_json({"order": order}, 201)
                if path == "/api/policy":
                    store.update_policy(body)
                    return self.send_json({"saved": True})
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["api", "orders"]:
                    order = store.transition(parts[2], parts[3], body.get("acknowledged", False))
                    return self.send_json({"order": order})
                raise DomainError("Not found.", 404)
            except DomainError as exc:
                LOG.warning("Request rejected: %s", exc)
                self.send_json({"error": str(exc)}, exc.status)
            except FoundryError as exc:
                LOG.warning("Foundry request failed: %s", exc)
                self.send_json({"error": str(exc)}, 502)
            except ValueError:
                LOG.error("Invalid local environment configuration")
                self.send_json({"error": "Invalid local .env configuration. Check setting names and syntax."}, 500)
            except sqlite3.Error:
                LOG.exception("Database operation failed")
                self.send_json({"error": "Local storage failed. Check disk access and server logs."}, 500)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "commerce.db")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                handler_for(Store(args.database), Foundry(), secrets.token_hex(32),
                                            Bank(args.database.parent / "banking.db")))
    LOG.info("Baku procurement sandbox: http://127.0.0.1:%s (simulated payments only)", server.server_port)
    LOG.info("Banking workflow prototype: http://127.0.0.1:%s/banking (manual, no microphone integration)", server.server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Stopping demo server")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
