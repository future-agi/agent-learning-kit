from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from fi.simulate.endpoints.vapi import VapiCallOriginator


def test_vapi_originator_posts_existing_resource_ids() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = request.content
        return httpx.Response(201, json={"id": "call_123", "status": "queued"})

    async def run() -> None:
        client = httpx.AsyncClient(
            base_url="https://api.vapi.ai",
            transport=httpx.MockTransport(handler),
        )
        originator = VapiCallOriginator(
            api_key="test-key",
            assistant_id="assistant_123",
            phone_number_id="phone_123",
            destination="+12065550100",
            client=client,
        )
        call = await originator.start()
        captured["marker"] = originator._call_marker
        await client.aclose()
        assert call.call_id == "call_123"
        assert call.status == "queued"

    asyncio.run(run())

    assert captured["path"] == "/call"
    assert captured["authorization"] == "Bearer test-key"
    assert json.loads(captured["body"]) == {
        "assistantId": "assistant_123",
        "phoneNumberId": "phone_123",
        "customer": {"number": "+12065550100"},
        "name": captured["marker"],
        "assistantOverrides": {
            "monitorPlan": {
                "controlEnabled": True,
                "controlAuthenticationEnabled": False,
            }
        },
    }
    assert len(captured["marker"]) <= 40


def test_vapi_originator_rejects_missing_response_id() -> None:
    async def run() -> None:
        client = httpx.AsyncClient(
            base_url="https://api.vapi.ai",
            transport=httpx.MockTransport(lambda _: httpx.Response(201, json={})),
        )
        originator = VapiCallOriginator(
            api_key="test-key",
            assistant_id="assistant_123",
            phone_number_id="phone_123",
            destination="+12065550100",
            client=client,
        )
        with pytest.raises(ValueError, match="vapi_call_response_missing_id"):
            await originator.start()
        await client.aclose()

    asyncio.run(run())


def _originator(client, *, public_api_key=None):
    originator = VapiCallOriginator(
        api_key="test-key",
        assistant_id="assistant_123",
        phone_number_id="phone_123",
        destination="+12065550100",
        client=client,
        public_api_key=public_api_key,
    )
    originator._POLL_INTERVAL_SECONDS = 0
    return originator


def test_authenticated_control_requires_public_key_before_dialing():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, json={"monitorPlan": {"controlAuthenticationEnabled": True}}
        )

    async def run():
        async with httpx.AsyncClient(
            base_url="https://api.vapi.ai", transport=httpx.MockTransport(handler)
        ) as client:
            originator = _originator(client)
            with pytest.raises(ValueError, match="public_api_key_required"):
                await originator.start()
            assert (
                await originator.reconcile_and_stop(
                    started_after_ms=1, ended_before_ms=2
                )
                == []
            )

    asyncio.run(run())
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/assistant/assistant_123")
    ]


@pytest.mark.parametrize("existing_authentication", [False, True])
def test_public_key_enables_authenticated_control_without_forwarding_private_key(
    existing_authentication,
):
    async def run():
        ended = False

        def handler(request):
            nonlocal ended
            if request.url.host == "calls.vapi.ai":
                assert request.headers["authorization"] == "Bearer public-test-key"
                assert "cookie" not in request.headers
                ended = True
                return httpx.Response(200)
            assert request.headers["authorization"] == "Bearer test-key"
            if request.url.path.startswith("/assistant/"):
                return httpx.Response(
                    200,
                    json={
                        "monitorPlan": {
                            "controlEnabled": False,
                            "controlAuthenticationEnabled": existing_authentication,
                        }
                    },
                )
            if request.url.path.startswith("/phone-number/"):
                return httpx.Response(200, json={})
            if request.method == "POST":
                assert json.loads(request.content)["assistantOverrides"][
                    "monitorPlan"
                ] == {
                    "controlEnabled": True,
                    "controlAuthenticationEnabled": True,
                }
                return httpx.Response(201, json={"id": "call_123"})
            return httpx.Response(
                200, json=_call(originator, status="ended" if ended else "in-progress")
            )

        async with httpx.AsyncClient(
            base_url="https://api.vapi.ai",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer do-not-forward"},
            cookies={"session": "private"},
        ) as client:
            originator = _originator(client, public_api_key="public-test-key")
            call = await originator.start()
            await originator.stop(call.call_id)
            assert ended

    asyncio.run(run())


@pytest.mark.parametrize(
    "plan", [[], False, "invalid", {"controlAuthenticationEnabled": "true"}]
)
def test_malformed_monitor_plan_fails_before_creating_call(plan):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"monitorPlan": plan})

    async def run():
        async with httpx.AsyncClient(
            base_url="https://api.vapi.ai", transport=httpx.MockTransport(handler)
        ) as client:
            originator = _originator(client)
            with pytest.raises(
                ValueError, match="vapi_(monitor_plan|control_authentication)_invalid"
            ):
                await originator.start()

    asyncio.run(run())
    assert [r.method for r in requests] == ["GET"]


def test_from_env_carries_public_control_key(monkeypatch):
    for key, value in {
        "VAPI_API_KEY": "private-test-key",
        "VAPI_PUBLIC_API_KEY": "public-test-key",
        "VAPI_ASSISTANT_ID": "assistant",
        "VAPI_PHONE_NUMBER_ID": "phone",
        "LIVEKIT_INBOUND_DID": "+15555550101",
    }.items():
        monkeypatch.setenv(key, value)

    async def run():
        originator = VapiCallOriginator.from_env()
        try:
            assert originator._public_api_key == "public-test-key"
        finally:
            await originator.close()

    asyncio.run(run())


def _call(originator, **overrides):
    return {
        "id": "call_123",
        "name": originator._call_marker,
        "assistantId": "assistant_123",
        "phoneNumberId": "phone_123",
        "customer": {"number": "+12065550100"},
        "status": "in-progress",
        "createdAt": "1970-01-01T00:00:01.500Z",
        "monitor": {"controlUrl": "https://calls.vapi.ai/call_123/control"},
        **overrides,
    }


def test_stop_uses_end_call_without_credentials_or_deleting_evidence():
    requests = []

    async def run():
        ended = False

        def handler(request):
            nonlocal ended
            requests.append(request)
            if request.method == "POST":
                assert request.url.host == "calls.vapi.ai"
                assert "authorization" not in request.headers
                assert "cookie" not in request.headers
                assert json.loads(request.content) == {"type": "end-call"}
                ended = True
                return httpx.Response(200)
            return httpx.Response(
                200, json=_call(originator, status="ended" if ended else "in-progress")
            )

        async with httpx.AsyncClient(
            base_url="https://api.vapi.ai",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer never-forward"},
            cookies={"session": "private"},
        ) as client:
            originator = _originator(client)
            await originator.stop("call_123")

    asyncio.run(run())
    assert [request.method for request in requests] == ["GET", "POST", "GET"]


@pytest.mark.parametrize(
    "control_url",
    [
        "http://calls.vapi.ai/call_123/control",
        "https://vapi.ai.evil.example/call_123/control",
        "https://localhost/call_123/control",
        "https://calls.vapi.ai/another_call/control",
        "https://user:password@calls.vapi.ai/call_123/control",
        "https://calls.vapi.ai:8443/call_123/control",
    ],
)
def test_stop_rejects_untrusted_or_wrong_call_control_urls(control_url):
    requests = []

    async def run():
        def handler(request):
            requests.append(request)
            return httpx.Response(
                200, json=_call(originator, monitor={"controlUrl": control_url})
            )

        async with httpx.AsyncClient(
            base_url="https://api.vapi.ai", transport=httpx.MockTransport(handler)
        ) as client:
            originator = _originator(client, public_api_key="public-test-key")
            with pytest.raises(ValueError, match="control_url_invalid"):
                await originator.stop("call_123")

    asyncio.run(run())
    assert [request.method for request in requests] == ["GET"]


def test_stop_does_not_claim_success_when_provider_never_confirms_ended():
    async def run():
        def handler(request):
            if request.method == "POST":
                return httpx.Response(200)
            return httpx.Response(200, json=_call(originator))

        async with httpx.AsyncClient(
            base_url="https://api.vapi.ai", transport=httpx.MockTransport(handler)
        ) as client:
            originator = _originator(client)
            originator._CLEANUP_TIMEOUT_SECONDS = 0.02
            with pytest.raises(asyncio.TimeoutError):
                await originator.stop("call_123")

    asyncio.run(run())


def test_parallel_lost_create_responses_reconcile_without_touching_other_calls():
    calls = {}
    stopped = []

    async def handler(request):
        if request.url.path.startswith("/assistant/"):
            return httpx.Response(200, json={})
        if request.url.path.startswith("/phone-number/"):
            return httpx.Response(200, json={})
        if request.url.path == "/call" and request.method == "POST":
            body = json.loads(request.content)
            call_id = f"call_{len(calls)}"
            calls[call_id] = {
                **body,
                "id": call_id,
                "status": "in-progress",
                "createdAt": "1970-01-01T00:00:01.500Z",
                "monitor": {"controlUrl": f"https://calls.vapi.ai/{call_id}/control"},
            }
            await asyncio.sleep(0)
            raise httpx.ReadTimeout("accepted but response lost", request=request)
        if request.url.path == "/call":
            return httpx.Response(
                200,
                json=[
                    *calls.values(),
                    {**next(iter(calls.values())), "id": "unrelated", "name": "other"},
                ],
            )
        if request.url.path.endswith("/control"):
            assert "authorization" not in request.headers
            call_id = request.url.path.split("/")[1]
            calls[call_id]["status"] = "ended"
            stopped.append(call_id)
            return httpx.Response(200)
        return httpx.Response(200, json=calls[request.url.path.rsplit("/", 1)[-1]])

    async def run():
        async with httpx.AsyncClient(
            base_url="https://api.vapi.ai", transport=httpx.MockTransport(handler)
        ) as client:
            first, second = _originator(client), _originator(client)
            errors = await asyncio.gather(
                first.start(), second.start(), return_exceptions=True
            )
            assert all(isinstance(error, httpx.ReadTimeout) for error in errors)
            assert first._call_marker != second._call_marker
            result = await asyncio.gather(
                *(
                    originator.reconcile_and_stop(
                        started_after_ms=1000, ended_before_ms=2000
                    )
                    for originator in (first, second)
                )
            )
            assert result == [["call_0"], ["call_1"]]
            with pytest.raises(ValueError, match="already_started"):
                await first.start()

    asyncio.run(run())
    assert sorted(stopped) == ["call_0", "call_1"]
