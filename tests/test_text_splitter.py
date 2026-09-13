import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from common.text_splitter import (
    combine_single_line_chunks,
    count_words,
    split_text_into_single_line_chunks,
    split_text_preserving_content,
)


def test_prefers_sentence_boundaries_and_preserves_every_character():
    text = (
        "  Một hai ba bốn năm sáu.  Bảy tám chín mười mười-một mười-hai!\r\n"
        "Mười-ba mười-bốn mười-lăm mười-sáu mười-bảy mười-tám.  "
    )

    chunks = split_text_preserving_content(text, 6, 8)

    assert "".join(chunks) == text
    assert chunks[0].endswith(".  ")
    assert all(count_words(chunk) <= 8 for chunk in chunks)
    assert all(count_words(chunk) >= 6 for chunk in chunks)


def test_uses_plain_word_boundary_when_sentence_has_no_punctuation():
    text = " ".join(f"từ{i}" for i in range(1, 32))
    chunks = split_text_preserving_content(text, 6, 25)
    assert "".join(chunks) == text
    assert [count_words(chunk) for chunk in chunks] == [25, 6]


def test_keeps_unavoidable_short_final_chunk_without_content_loss():
    text = " ".join(f"word{i}" for i in range(26))
    chunks = split_text_preserving_content(text, 25, 25)
    assert "".join(chunks) == text
    assert [count_words(chunk) for chunk in chunks] == [25, 1]


def test_empty_text_returns_no_chunks():
    assert split_text_preserving_content("", 6, 25) == []


def test_each_source_line_becomes_a_separate_single_line_file():
    text = (
        "High above the tree line in the Himalayas,\r\n"
        "a structural failure begins in absolute silence.\r\n"
    )

    chunks = split_text_into_single_line_chunks(text, 6, 25)

    assert chunks == [
        "High above the tree line in the Himalayas,",
        "a structural failure begins in absolute silence.",
    ]
    assert all("\n" not in chunk and "\r" not in chunk for chunk in chunks)


def test_long_source_line_is_split_but_every_chunk_stays_one_line():
    text = " ".join(f"word{i}" for i in range(31))
    chunks = split_text_into_single_line_chunks(text, 6, 25)
    assert [count_words(chunk) for chunk in chunks] == [25, 6]
    assert all("\n" not in chunk and "\r" not in chunk for chunk in chunks)


def test_combined_source_has_one_line_matching_each_child_file():
    chunks = ["Nội dung file một.", "Nội dung file hai.", "File ba."]

    combined = combine_single_line_chunks(chunks)

    assert combined.splitlines() == chunks
    assert len(combined.splitlines()) == len(chunks)


def test_combining_rejects_multiline_child_content():
    try:
        combine_single_line_chunks(["một dòng", "hai\ndòng"])
    except ValueError as exc:
        assert "đúng một dòng" in str(exc)
    else:
        raise AssertionError("Nội dung file con nhiều dòng phải bị từ chối")
