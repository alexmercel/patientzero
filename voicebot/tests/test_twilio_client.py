from __future__ import annotations

from types import SimpleNamespace

import pytest

from voicebot.src.telephony import twilio_client as twilio_module
from voicebot.src.telephony.twilio_client import TwilioClient, TwilioClientError


class FakeVoiceStream:
    def __init__(self, url: str) -> None:
        self.url = url
        self.parameters: list[tuple[str, str]] = []

    def parameter(self, name: str, value: str) -> None:
        self.parameters.append((name, value))


class FakeConnect:
    def __init__(self) -> None:
        self.stream_obj: FakeVoiceStream | None = None

    def stream(self, url: str) -> FakeVoiceStream:
        self.stream_obj = FakeVoiceStream(url)
        return self.stream_obj


class FakeVoiceResponse:
    def __init__(self) -> None:
        self.connect_obj = FakeConnect()

    def connect(self) -> FakeConnect:
        return self.connect_obj

    def __str__(self) -> str:
        url = self.connect_obj.stream_obj.url if self.connect_obj.stream_obj else ""
        return f"<Response><Connect><Stream url=\"{url}\" /></Connect></Response>"


class FakeAccountsApi:
    def __init__(self) -> None:
        self.sid = "AC123"

    def accounts(self, sid: str) -> "FakeAccountsApi":
        self.sid = sid
        return self

    def fetch(self) -> SimpleNamespace:
        return SimpleNamespace(sid=self.sid)


class FakeCallsApi:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.last_kwargs: dict[str, object] | None = None
        self.updated_call_sid: str | None = None
        self.updated_status: str | None = None

    def create(self, **kwargs: object) -> SimpleNamespace:
        if self.should_fail:
            raise RuntimeError("boom")
        self.last_kwargs = kwargs
        return SimpleNamespace(sid="CA123", status="queued")

    def __call__(self, call_sid: str) -> "FakeCallsApi":
        self.updated_call_sid = call_sid
        return self

    def update(self, *, status: str) -> SimpleNamespace:
        if self.should_fail:
            raise RuntimeError("boom")
        self.updated_status = status
        return SimpleNamespace(sid=self.updated_call_sid, status=status)


class FakeRestClient:
    def __init__(self, should_fail: bool = False) -> None:
        self.api = FakeAccountsApi()
        self.calls = FakeCallsApi(should_fail=should_fail)


def test_validate_credentials(settings) -> None:
    client = TwilioClient(settings, client=FakeRestClient())
    assert client.validate_credentials() == "AC123"


def test_initiate_outbound_call(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(twilio_module, "VoiceResponse", FakeVoiceResponse)
    rest_client = FakeRestClient()
    client = TwilioClient(settings, client=rest_client)

    metadata = client.initiate_outbound_call(
        to_number="+15555550124",
        stream_url="wss://example.test/ws/twilio-media",
        custom_parameters={"callSidHint": "hint"},
    )

    assert metadata.call_sid == "CA123"
    assert rest_client.calls.last_kwargs is not None
    assert rest_client.calls.last_kwargs["record"] is True
    assert "twiml" in rest_client.calls.last_kwargs


def test_initiate_outbound_call_wraps_errors(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(twilio_module, "VoiceResponse", FakeVoiceResponse)
    client = TwilioClient(settings, client=FakeRestClient(should_fail=True))

    with pytest.raises(TwilioClientError):
        client.initiate_outbound_call(
            to_number="+15555550124",
            stream_url="wss://example.test/ws/twilio-media",
        )


def test_end_call_updates_twilio_call_status(settings) -> None:
    rest_client = FakeRestClient()
    client = TwilioClient(settings, client=rest_client)

    status = client.end_call("CA123")

    assert status == "completed"
    assert rest_client.calls.updated_call_sid == "CA123"
    assert rest_client.calls.updated_status == "completed"
