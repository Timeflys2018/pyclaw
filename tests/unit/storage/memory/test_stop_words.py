from __future__ import annotations

from pyclaw.storage.memory.stop_words import (
    CHINESE_STOP_WORDS,
    ENGLISH_STOP_WORDS,
    STOP_WORDS,
)


def test_stop_words_excludes_programming_keywords() -> None:
    programming_keywords = {'if', 'for', 'in', 'is', 'as', 'or', 'not', 'do', 'go', 'no', 'be'}
    for kw in programming_keywords:
        assert kw not in STOP_WORDS, f"programming keyword '{kw}' should NOT be in STOP_WORDS"


def test_stop_words_contains_common_chinese_particles() -> None:
    must_contain = {'的', '了', '是', '在', '我', '你', '他', '她', '它', '们'}
    for w in must_contain:
        assert w in STOP_WORDS, f"common Chinese particle '{w}' should be in STOP_WORDS"


def test_stop_words_contains_common_english_articles() -> None:
    must_contain = {'the', 'a', 'an'}
    for w in must_contain:
        assert w in STOP_WORDS, f"English article '{w}' should be in STOP_WORDS"


def test_stop_words_is_union_of_chinese_and_english() -> None:
    assert STOP_WORDS == CHINESE_STOP_WORDS | ENGLISH_STOP_WORDS


def test_stop_words_reasonable_size() -> None:
    assert 150 < len(STOP_WORDS) < 300
