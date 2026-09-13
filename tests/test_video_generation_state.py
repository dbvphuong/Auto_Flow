from core.automations import video_fx


def test_recent_progress_ignores_transient_agent_error():
    assert video_fx._is_generation_failure_candidate(
        progress=None,
        seconds_since_progress=3,
        tile_failed=False,
        agent_error=True,
        queued=False,
        progress_seen=True,
    ) is False


def test_queued_agent_message_is_not_terminal_failure():
    assert video_fx._is_generation_failure_candidate(
        progress=None,
        seconds_since_progress=60,
        tile_failed=False,
        agent_error=True,
        queued=True,
        progress_seen=True,
    ) is False


def test_scoped_tile_failure_is_candidate_after_grace_period():
    assert video_fx._is_generation_failure_candidate(
        progress=None,
        seconds_since_progress=31,
        tile_failed=True,
        agent_error=False,
        queued=False,
        progress_seen=True,
    ) is True


def test_global_agent_failure_only_applies_before_real_progress():
    assert video_fx._is_generation_failure_candidate(
        progress=None,
        seconds_since_progress=31,
        tile_failed=False,
        agent_error=True,
        queued=False,
        progress_seen=False,
    ) is True
    assert video_fx._is_generation_failure_candidate(
        progress=None,
        seconds_since_progress=31,
        tile_failed=False,
        agent_error=True,
        queued=False,
        progress_seen=True,
    ) is False


def test_live_progress_wins_over_duplicate_error_card():
    assert video_fx._is_generation_failure_candidate(
        progress=42,
        seconds_since_progress=0,
        tile_failed=False,
        agent_error=True,
        queued=True,
        progress_seen=True,
        terminal_error=True,
    ) is False


def test_explicit_no_charge_error_is_terminal_once_progress_disappears():
    assert video_fx._is_generation_failure_candidate(
        progress=None,
        seconds_since_progress=0,
        tile_failed=False,
        agent_error=True,
        queued=True,
        progress_seen=True,
        terminal_error=True,
    ) is True


def test_policy_rejection_is_never_retryable_even_before_progress():
    text = (
        "Không thành công. Câu lệnh này có thể vi phạm chính sách của "
        "chúng tôi về việc tạo video về người nổi tiếng."
    )
    assert video_fx._terminal_generation_error_retryable(text, False) is False


def test_generic_terminal_error_after_real_progress_is_retryable():
    assert video_fx._terminal_generation_error_retryable(
        "Không thành công. Rất tiếc, đã xảy ra lỗi!", True
    ) is True


def test_transient_terminal_error_before_progress_can_retry():
    assert video_fx._terminal_generation_error_retryable(
        "Không thành công. Rất tiếc, đã xảy ra lỗi!", False
    ) is True


def test_current_invalid_prompt_card_before_progress_is_not_retryable():
    assert video_fx._terminal_generation_error_retryable(
        "Không thành công. Không tải được video.", False
    ) is False


def test_same_generic_card_after_progress_can_retry():
    assert video_fx._terminal_generation_error_retryable(
        "Không thành công. Không tải được video.", True
    ) is True


def test_new_failure_card_ignores_cards_present_before_submit():
    records = [
        {"key": "old", "text": "Không thành công"},
        {"key": "new", "text": "Không thành công Không tải được video."},
    ]
    assert video_fx._new_visible_failure(records, {"old"}) == records[1]


def test_new_failure_card_returns_none_for_only_stale_card():
    records = [{"key": "old", "text": "Không thành công"}]
    assert video_fx._new_visible_failure(records, {"old"}) is None
