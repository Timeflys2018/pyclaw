from __future__ import annotations

import logging

import apsw
import apsw.fts5

logging.getLogger("jieba").setLevel(logging.WARNING)

import jieba

jieba.setLogLevel(logging.WARNING)
jieba.initialize()

from pyclaw.storage.memory.stop_words import STOP_WORDS


@apsw.fts5.StringTokenizer
def jieba_tokenizer(con: apsw.Connection, params: list[str]):
    def tokenize(text: str, flags: int, locale: str | None):
        offset = 0
        for word in jieba.cut_for_search(text):
            word_stripped = word.strip()
            if not word_stripped:
                continue
            search_from = max(0, offset - len(word_stripped) - 1)
            start = text.find(word_stripped, search_from)
            if start == -1:
                start = text.find(word_stripped)
            if start == -1:
                continue
            end = start + len(word_stripped)
            if word_stripped.lower() not in STOP_WORDS:
                yield start, end, word_stripped.lower()
            offset = max(offset, end)

    return tokenize


def build_safe_match_query(query: str) -> str | None:
    tokens = []
    for word in jieba.cut(query, cut_all=False):
        word = word.strip()
        if not word or word.lower() in STOP_WORDS:
            continue
        escaped = word.replace('"', '""')
        tokens.append(f'"{escaped}"')
    if not tokens:
        return None
    return " OR ".join(tokens)


def register_jieba_tokenizer(conn: apsw.Connection) -> None:
    conn.register_fts5_tokenizer("jieba", jieba_tokenizer)
