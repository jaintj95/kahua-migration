"""Small streaming Kahua client. Only reads are retried automatically."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import time
from urllib.parse import urlencode, urlsplit

from .preflight import InputError


class ApiError(RuntimeError):
    pass


class KahuaClient:
    def __init__(self, origin, client_id, client_secret, *, timeout=120,
                 allow_local_mock=False, sleep=time.sleep):
        parsed = urlsplit(origin)
        local_mock = (allow_local_mock and parsed.scheme == "http"
                      and parsed.hostname in {"127.0.0.1", "localhost", "::1"})
        if (not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or (not local_mock and (parsed.scheme != "https" or not any(
                    parsed.hostname.endswith("." + d) for d in ("kahua.com", "kahuafn.com"))))):
            raise InputError("Use an HTTPS Kahua service origin without a path or credentials")
        if not client_id or not client_secret:
            raise InputError("Kahua OAuth client ID and secret are required")
        self.origin = origin.rstrip("/")
        self.hostname, self.port = parsed.hostname, parsed.port
        self.connection_type = http.client.HTTPConnection if local_mock else http.client.HTTPSConnection
        self.basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self.timeout, self.sleep = timeout, sleep
        self.token, self.expires = None, 0

    def _send(self, method, path, headers, body=None, hash_limit=None):
        if not path.startswith("/") or path.startswith("//") or "\r" in path or "\n" in path:
            raise InputError("Invalid API path")
        conn = self.connection_type(self.hostname, self.port, timeout=self.timeout)
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            response_headers = {k.lower(): v for k, v in response.getheaders()}
            if hash_limit is not None and response.status == 200:
                count, sha = 0, hashlib.sha256()
                while chunk := response.read(min(1024 * 1024, hash_limit - count + 1)):
                    count += len(chunk)
                    if count > hash_limit:
                        raise ApiError("Downloaded file is larger than the expected source")
                    sha.update(chunk)
                data = {"size_bytes": count, "sha256": sha.hexdigest()}
            else:
                data = response.read(32 * 1024 * 1024 + 1)
                if len(data) > 32 * 1024 * 1024:
                    raise ApiError("API response exceeded the 32 MiB limit")
            return response.status, data, response_headers
        except (OSError, http.client.HTTPException) as exc:
            # Avoid logging credentials, request headers or response bodies.
            raise ApiError(f"Kahua connection failed: {type(exc).__name__}") from exc
        finally:
            conn.close()

    def _authenticate(self):
        status, data, _ = self._send("POST", "/oauth2/token", {
            "Authorization": "Basic " + self.basic,
            "Content-Type": "application/x-www-form-urlencoded",
        }, urlencode({"grant_type": "client_credentials"}))
        if status != 200:
            raise ApiError(f"OAuth failed with HTTP {status}")
        try:
            payload = json.loads(data)
            if not payload["access_token"] or payload["token_type"].lower() != "bearer":
                raise ValueError()
            self.token = payload["access_token"]
            self.expires = time.monotonic() + max(0, float(payload["expires_in"]) - 15)
        except (ValueError, KeyError, TypeError) as exc:
            raise ApiError("OAuth response did not contain a usable bearer token") from exc

    def request(self, method, path, *, body=None, content_type=None, size=None,
                read_only=False, hash_limit=None):
        # Read-only POST is allowed only for Kahua's documented query endpoint.
        if read_only and method != "GET" and not (method == "POST" and urlsplit(path).path.endswith("/query")):
            raise InputError("Only GET and /query calls may use read retries")
        attempts = 3 if read_only else 1
        for attempt in range(attempts):
            if not self.token or time.monotonic() >= self.expires:
                self._authenticate()
            headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
            if content_type:
                headers["Content-Type"] = content_type
            if size is not None:
                headers["Content-Length"] = str(size)
            try:
                status, data, response_headers = self._send(method, path, headers, body, hash_limit)
            except ApiError:
                if attempt + 1 == attempts:
                    raise
                self.sleep(2 ** attempt)
                continue
            if 200 <= status < 300:
                return data, response_headers
            if read_only and attempt + 1 < attempts and status in (401, 408, 429, 500, 502, 503, 504):
                if status == 401:
                    self.token = None
                try:
                    delay = float(response_headers.get("retry-after", 2 ** attempt))
                except ValueError:
                    delay = 2 ** attempt
                if delay > 60:
                    raise ApiError(f"Kahua requested a read retry after {delay:g}s; resume later")
                self.sleep(max(0, delay))
                continue
            raise ApiError(f"Kahua request failed with HTTP {status}; mutations are never automatically retried")
        raise ApiError("Read retry limit reached")

    def json(self, method, path, payload=None, *, read_only=False):
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        data, _ = self.request(method, path, body=body, content_type="application/json" if body else None,
                               read_only=read_only)
        try:
            result = json.loads(data)
        except (ValueError, TypeError) as exc:
            raise ApiError("Kahua returned a non-JSON response") from exc
        if not isinstance(result, dict):
            raise ApiError("Expected a JSON object from Kahua")
        return result

    def get(self, path):
        data, headers = self.request("GET", path, read_only=True)
        return data, headers.get("content-type", "")

    def upload(self, path, handle, size):
        data, _ = self.request("POST", path, body=handle,
                               content_type="application/octet-stream", size=size)
        try:
            result = json.loads(data)
        except ValueError as exc:
            raise ApiError("Upload response was not JSON; reconcile before retrying") from exc
        if not isinstance(result, dict) or not result.get("entityId"):
            raise ApiError("Upload response has no file ID; reconcile before retrying")
        return result

    def hash_download(self, path, expected_size):
        data, _ = self.request("GET", path, read_only=True, hash_limit=expected_size)
        return data
