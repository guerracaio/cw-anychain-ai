import asyncio
import json
import logging
import math
import time
from contextvars import ContextVar

import httpx
from pydantic import JsonValue

request_id: ContextVar[str] = ContextVar("request_id", default="-")
logger = logging.getLogger("anychain.tools")
# GitHub answers rate limits for anonymous clients with 403, so it is not retried.
STATUS_CODES = {401: "access_denied", 403: "access_denied", 404: "not_found"}


def parse_json(data: bytearray) -> JsonValue:
    payload = json.loads(data)
    pending = [(payload, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 64 or isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Invalid JSON value or nesting")
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
    return payload


class UpstreamError(Exception):
    """Only safe, application-owned codes cross the service boundary."""

    def __init__(self, code: str, status: int | None = None):
        self.code = code
        self.status = status
        super().__init__(code)


class JsonHttpClient:
    def __init__(self, client: httpx.AsyncClient, timeout: float, max_bytes: int):
        self.client = client
        self.timeout = timeout
        self.max_bytes = max_bytes

    async def request(
        self,
        method: str,
        url: str,
        *,
        tool: str,
        params: dict[str, str] | None = None,
        body: dict | None = None,
        max_bytes: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> JsonValue:
        limit = max_bytes or self.max_bytes
        start = time.monotonic()
        code = "ok"
        status = None
        try:
            async with asyncio.timeout(self.timeout):
                for attempt in range(2):
                    try:
                        async with self.client.stream(
                            method,
                            url,
                            params=params,
                            json=body,
                            headers=headers,
                            timeout=self.timeout,
                            follow_redirects=False,
                        ) as response:
                            status = response.status_code
                            if status == 429 or status in (502, 503, 504):
                                raise UpstreamError("temporarily_unavailable", status)
                            if status != 200:
                                code = STATUS_CODES.get(status, "http_error")
                                raise UpstreamError(code, status)
                            data = bytearray()
                            async for chunk in response.aiter_bytes():
                                data.extend(chunk)
                                if len(data) > limit:
                                    raise UpstreamError("payload_limit")
                            try:
                                return parse_json(data)
                            except (ValueError, UnicodeError, RecursionError):
                                raise UpstreamError("invalid_json") from None
                    except (httpx.TimeoutException, httpx.NetworkError):
                        if attempt:
                            raise UpstreamError("connection_failed") from None
                    except UpstreamError as exc:
                        if attempt or exc.code != "temporarily_unavailable":
                            raise
                    await asyncio.sleep(0.2)
        except TimeoutError:
            code = "timeout"
            raise UpstreamError(code) from None
        except UpstreamError as exc:
            code = exc.code
            raise
        except httpx.HTTPError:
            code = "connection_failed"
            raise UpstreamError(code) from None
        except asyncio.CancelledError:
            code = "cancelled"
            raise
        finally:
            # Never log URLs, response bodies, exception strings or credentials.
            logger.info(
                "request_id=%s tool=%s duration_ms=%d result=%s http_status=%s",
                request_id.get(),
                tool,
                (time.monotonic() - start) * 1000,
                code,
                status,
            )
        raise UpstreamError("connection_failed")
