import pytest

from core.automations import video_fx
from core.automations.video_fx import FlowGenerationFailed
from core.workers import _can_retry_flow_task


def test_worker_honors_non_retryable_generation_exception():
    error = FlowGenerationFailed("generic terminal error", retryable=False)
    assert _can_retry_flow_task(str(error), error) is False


def test_worker_does_not_retry_policy_rejection_from_plain_message():
    message = "Câu lệnh này có thể vi phạm chính sách về nội dung gây hại."
    assert _can_retry_flow_task(message) is False


def test_worker_can_retry_a_transient_failure_before_generation_started():
    error = FlowGenerationFailed("Agent is overloaded", retryable=True)
    assert _can_retry_flow_task(str(error), error) is True


def test_run_video_preserves_retry_decision_from_generation(monkeypatch):
    expected = FlowGenerationFailed("accepted request failed", retryable=False)

    def fail_once(*_args, **_kwargs):
        raise expected

    monkeypatch.setattr(video_fx, "_run_video_fx_once", fail_once)
    with pytest.raises(FlowGenerationFailed) as raised:
        video_fx.run_video_fx(None, None, "prompt", 1, {})

    assert raised.value is expected
    assert raised.value.retryable is False
