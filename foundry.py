"""Keyless Foundry REST adapter; only explicit synthetic demo inputs are sent."""

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse
from settings import load_config

ROOT = Path(__file__).resolve().parent


class FoundryError(Exception):
    pass


class Foundry:
    def __init__(self, config=None):
        self.config = load_config() if config is None else config
        endpoint = urlparse(self.config["project_endpoint"])
        if self.config["project_endpoint"] and (endpoint.scheme != "https"
                or not endpoint.hostname
                or not endpoint.hostname.endswith(".services.ai.azure.com")
                or endpoint.username or endpoint.password or endpoint.query
                or endpoint.fragment or endpoint.port not in (None, 443)):
            raise ValueError("A canonical HTTPS Foundry project endpoint is required.")
        self._token = None
        self._expires = 0
        self._lock = threading.Lock()

    def token(self):
        if not self.config["subscription_id"] or not self.config["tenant_id"]:
            raise FoundryError("Set AZURE_SUBSCRIPTION_ID and AZURE_TENANT_ID in your local .env.")
        with self._lock:
            if self._token and time.time() < self._expires - 120:
                return self._token
            if os.environ.get("IDENTITY_ENDPOINT"):
                from azure.identity import ManagedIdentityCredential
                from azure.core.exceptions import AzureError
                try:
                    value = ManagedIdentityCredential().get_token("https://ai.azure.com/.default")
                except AzureError as exc:
                    raise FoundryError("Managed identity could not obtain a Foundry token.") from exc
                self._token, self._expires = value.token, value.expires_on
                return self._token
            az = shutil.which("az")
            if not az:
                raise FoundryError("Azure CLI is missing. Install it and run az login.")
            try:
                result = subprocess.run(
                    [az, "account", "get-access-token", "--subscription",
                     self.config["subscription_id"],
                     "--resource", "https://ai.azure.com", "--output", "json"],
                    capture_output=True, text=True, timeout=40, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise FoundryError("Could not run Azure CLI. Check your local sign-in.") from exc
            if result.returncode:
                raise FoundryError("Azure sign-in failed. Run az login for the configured tenant.")
            try:
                value = json.loads(result.stdout)
                if value.get("tenant") != self.config["tenant_id"]:
                    raise ValueError("Unexpected token tenant")
                self._token = value["accessToken"]
                self._expires = float(value["expires_on"])
            except (ValueError, KeyError, TypeError) as exc:
                raise FoundryError("Azure CLI returned an invalid token response.") from exc
            return self._token

    def request(self, method, path, body=None, headers=None):
        if not self.config["project_endpoint"]:
            raise FoundryError("Configure the Foundry project endpoint in your local .env before live calls.")
        encoded = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.config["project_endpoint"].rstrip("/") + path,
            data=encoded, method=method,
            headers={"Authorization": "Bearer " + self.token(),
                     "Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=75) as response:
                result = json.load(response)
                if not isinstance(result, dict):
                    raise ValueError("Expected an object response")
                return result
        except urllib.error.HTTPError as exc:
            messages = {
                401: "Foundry authentication expired. Sign in again with Azure CLI.",
                403: "Foundry denied access. Check project-scoped Foundry User permissions.",
                404: "Foundry agent or deployment was not found. Verify your local .env and project provisioning.",
                429: "Foundry is rate limited. Wait and retry this request.",
            }
            error = FoundryError(messages.get(
                exc.code, f"Foundry returned HTTP {exc.code}. Check the project in Azure."
            ))
            error.status = exc.code
            raise error from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise FoundryError("Foundry could not be reached. Check the network and retry.") from exc
        except (ValueError, UnicodeError) as exc:
            raise FoundryError("Foundry returned an invalid JSON response.") from exc

    def recommend(self, request, catalog, quantity, policy):
        if not self.config["agent_name"]:
            raise FoundryError("Set COMMERCE_AGENT_NAME in your local .env before live recommendations.")
        response = self.request("POST", "/openai/v1/responses", {
            "agent_reference": {
                "type": "agent_reference", "name": self.config["agent_name"],
                "version": self.config["agent_version"],
            },
            "input": json.dumps({"request": request, "catalog": catalog,
                                 "quantity": quantity, "policy": policy}),
            "store": False,
            "max_output_tokens": 500,
        })
        if response.get("status") != "completed":
            raise FoundryError("The agent did not complete its recommendation. Retry the request.")
        try:
            text = "".join(
                part["text"] for item in response["output"] if item.get("type") == "message"
                for part in item.get("content", []) if part.get("type") == "output_text"
            )
            decision = json.loads(text)
            if not isinstance(decision, dict):
                raise ValueError("Expected an object")
            product_id, reason = decision["product_id"], decision["reason"]
            if product_id is not None and product_id not in {p["id"] for p in catalog}:
                raise ValueError("Unknown product")
            if not isinstance(reason, str) or not 1 <= len(reason) <= 600:
                raise ValueError("Invalid explanation")
        except (KeyError, TypeError, ValueError) as exc:
            raise FoundryError("The agent returned an invalid recommendation; no order was created.") from exc
        if product_id is None:
            raise FoundryError("The agent found no suitable catalog item. Refine the demo request.")
        return product_id, reason, response.get("id")
