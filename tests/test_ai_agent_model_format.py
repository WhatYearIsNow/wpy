"""模型偶发无效格式只重试决策，不放宽参数或重做论坛动作。"""

from urllib.error import HTTPError

import pytest

from wpy import ai_agent as ai
from .test_ai_agent import Remote


def test_invalid_response_can_recover_within_existing_step_budget():
    calls = []
    remote = Remote()

    def request(*args, **kwargs):
        calls.append(kwargs)
        content = "bad format" if len(calls) == 1 else '{"action":"stop","arguments":{}}'
        return {"choices": [{"message": {"content": content}}]}

    model = ai.Model(
        ai.ModelConfig("https://example.invalid", "test_key", "test-model"), request=request
    )
    result = ai.cycle(model, ai.Executor(ai.Forum("test_personal_token", request=remote), None))
    assert result == {"reads": 0, "writes": 0, "previews": 0, "denied": 1}
    assert len(calls) == 2 and not remote.calls


def test_invalid_responses_never_exceed_eight_model_decisions():
    calls = []
    remote = Remote()

    def request(*args, **kwargs):
        calls.append(kwargs)
        return {"choices": [{"message": {"content": '{"action":"delete_post","arguments":{}}'}}]}

    model = ai.Model(
        ai.ModelConfig("https://example.invalid", "test_key", "test-model"), request=request
    )
    with pytest.raises(ai.ModelFormatError):
        ai.cycle(model, ai.Executor(ai.Forum("test_personal_token", request=remote), None))
    assert len(calls) == 8 and not remote.calls


@pytest.mark.parametrize(
    "response",
    [
        '```\n{"action":"stop","arguments":{}}\n```',
        '```JSON\n{"action":"stop","arguments":{}}\n```',
    ],
)
def test_common_json_fences_still_require_the_same_strict_action_schema(response):
    model = ai.Model(
        ai.ModelConfig("https://example.invalid", "test_key", "test-model"),
        request=lambda *a, **kw: {"choices": [{"message": {"content": response}}]},
    )
    assert model.next([]) == {"action": "stop", "arguments": {}}


@pytest.mark.parametrize("http", [False, True])
def test_transport_failures_are_not_retried_as_bad_json_and_do_not_leak_responses(http):
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs)
        if http:
            raise HTTPError("https://example.invalid", 503, "test_private_response", {}, None)
        raise OSError("test_private_connection_details")

    model = ai.Model(
        ai.ModelConfig("https://example.invalid", "test_key", "test-model"), request=request
    )
    with pytest.raises(ai.AgentError) as error:
        ai.cycle(model, ai.Executor(ai.Forum("test_personal_token"), None))
    assert not isinstance(error.value, ai.ModelFormatError)
    assert "test_private" not in str(error.value) and len(calls) == 1
