"""Failure classification and response parsing for the posting-service client (no network: httpx.MockTransport)."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from app.services.mock_client import FailureKind, MockPostingServiceClient, PostingPayload, PostingServiceError, response_indicates_exists

TX = uuid4()


def client_for(handler) -> MockPostingServiceClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    return MockPostingServiceClient("http://mock", http=http)


def payload() -> PostingPayload:
    return PostingPayload(TX, Decimal("100.50"), "USD", "d", datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc), {"k": 1})


async def test_exists_true_false_and_error():
    assert await client_for(lambda r: httpx.Response(200, json={"id": str(TX)})).exists(TX) is True
    assert await client_for(lambda r: httpx.Response(404, json={"error": "nf"})).exists(TX) is False
    with pytest.raises(PostingServiceError) as exc:
        await client_for(lambda r: httpx.Response(500)).exists(TX)
    assert exc.value.kind is FailureKind.UNCERTAIN  # a failed lookup is never proof of absence


@pytest.mark.parametrize("response,expected", [
    (httpx.Response(200, json={"id": "x"}), True), (httpx.Response(200, json={"transactionId": "x"}), True),
    (httpx.Response(200, json={"transaction": {"id": "x"}}), True), (httpx.Response(200, json={"transaction": None}), False),
    (httpx.Response(200, json={"exists": False}), False), (httpx.Response(200, json=[]), False), (httpx.Response(200, json={}), False),
    (httpx.Response(404), False),
])
def test_get_response_shapes(response, expected):
    assert response_indicates_exists(response) is expected


async def test_post_success_and_body_shape():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = request.read()
        return httpx.Response(201, json={"ok": True})

    await client_for(handler).post(payload())
    import json
    body = json.loads(seen["body"])
    assert body["id"] == str(TX) and body["amount"] == 100.5 and body["timestamp"] == "2025-01-01T12:00:00.000Z" and body["metadata"] == {"k": 1}


@pytest.mark.parametrize("status,kind", [
    (500, FailureKind.UNCERTAIN), (502, FailureKind.UNCERTAIN), (503, FailureKind.UNCERTAIN), (409, FailureKind.UNCERTAIN),
    (429, FailureKind.NOT_WRITTEN), (400, FailureKind.REJECTED), (422, FailureKind.REJECTED),
])
async def test_post_status_classification(status, kind):
    with pytest.raises(PostingServiceError) as exc:
        await client_for(lambda r: httpx.Response(status)).post(payload())
    assert exc.value.kind is kind


@pytest.mark.parametrize("error,kind,code", [
    (httpx.ConnectError("refused"), FailureKind.NOT_WRITTEN, "CONNECTION_ERROR"),
    (httpx.ConnectTimeout("slow"), FailureKind.NOT_WRITTEN, "CONNECTION_ERROR"),
    (httpx.ReadTimeout("slow"), FailureKind.UNCERTAIN, "TIMEOUT"),
    (httpx.RemoteProtocolError("reset"), FailureKind.UNCERTAIN, "TRANSPORT_ERROR"),
])
async def test_post_transport_classification(error, kind, code):
    def handler(request):
        raise error

    with pytest.raises(PostingServiceError) as exc:
        await client_for(handler).post(payload())
    assert exc.value.kind is kind and exc.value.code == code


async def test_client_errors_do_not_count_against_breaker():
    with pytest.raises(PostingServiceError) as exc:
        await client_for(lambda r: httpx.Response(400)).post(payload())
    assert exc.value.counts_for_breaker is False
    with pytest.raises(PostingServiceError) as exc:
        await client_for(lambda r: httpx.Response(500)).post(payload())
    assert exc.value.counts_for_breaker is True
