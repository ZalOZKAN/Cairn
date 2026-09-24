"""Scoring logic for scripts/benchmark_external.py, no network, no real
dataset download. Tiny synthetic fixtures matching the real LoCoMo /
LongMemEval-S schema (verified against the actual files during Step 5)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from benchmark_external import run_locomo, run_longmemeval  # noqa: E402


def test_run_locomo_scores_a_findable_evidence_turn():
    fixture = [{
        "sample_id": "conv-test",
        "conversation": {
            "session_1_date_time": "1 January 2024",
            "session_1": [
                {"speaker": "Alice", "dia_id": "D1:1", "text": "we picked postgres for the main database"},
                {"speaker": "Bob", "dia_id": "D1:2", "text": "sounds good to me"},
            ],
        },
        "qa": [
            {"question": "what database did we pick", "answer": "postgres", "evidence": ["D1:1"], "category": 1},
        ],
    }]
    result = run_locomo("sqlite", n_convs=1, k=5, data=fixture)
    assert result["items"] == 1
    assert result["hits"] == 1
    assert result["evidence_recall"] == 1.0


def test_run_locomo_skips_qa_with_no_resolvable_evidence():
    fixture = [{
        "sample_id": "conv-test",
        "conversation": {
            "session_1_date_time": "1 January 2024",
            "session_1": [{"speaker": "Alice", "dia_id": "D1:1", "text": "hello"}],
        },
        "qa": [{"question": "unanswerable", "answer": "?", "evidence": ["D9:99"], "category": 5}],
    }]
    result = run_locomo("sqlite", n_convs=1, k=5, data=fixture)
    assert result["items"] == 0  # evidence id doesn't resolve to a stored turn, not scored


def test_run_longmemeval_scores_answer_session_hit():
    fixture = [{
        "question_id": "q1",
        "question": "what degree did I graduate with",
        "answer": "Business Administration",
        "haystack_session_ids": ["s1", "s2"],
        "haystack_sessions": [
            [{"role": "user", "content": "I graduated with a degree in Business Administration"}],
            [{"role": "user", "content": "totally unrelated filler content about the weather"}],
        ],
        "answer_session_ids": ["s1"],
    }]
    result = run_longmemeval("sqlite", n_questions=1, k=5, data=fixture)
    assert result["items"] == 1
    assert result["hits"] == 1


def test_run_longmemeval_skips_question_with_no_answer_session_ids():
    fixture = [{
        "question_id": "q2",
        "question": "irrelevant",
        "answer": "n/a",
        "haystack_session_ids": ["s1"],
        "haystack_sessions": [[{"role": "user", "content": "some content"}]],
        "answer_session_ids": [],
    }]
    result = run_longmemeval("sqlite", n_questions=1, k=5, data=fixture)
    assert result["items"] == 0
