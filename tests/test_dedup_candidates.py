"""candidate_relation thresholds, calibrated with scripts/calibrate_candidates.py."""
from __future__ import annotations

import pytest

from cairn.memory.dedup import candidate_relation, conflict_relation


@pytest.mark.parametrize("left,right", [
    ("Use SQLite for local storage", "Use SQLite for the local storage layer"),
    ("Log structured JSON from the CLI", "The CLI logs structured JSON"),
    ("Run ruff in CI before the test job", "In CI run ruff before the tests job"),
])
def test_rewordings_are_possible_duplicates(left, right):
    assert candidate_relation(left, right)[0] == "possible-duplicate"


@pytest.mark.parametrize("left,right", [
    ("Use Redis for the cache", "Do not use Redis for the cache"),
    ("Retry failed requests 3 times", "Retry failed requests 5 times"),
    ("Support Python 3.10 and newer", "Support Python 3.12 and newer"),
])
def test_negation_and_number_changes_are_never_duplicates(left, right):
    assert candidate_relation(left, right)[0] == "protected-difference"


def test_unrelated_decisions_are_not_candidates():
    assert candidate_relation("Use SQLite for local storage", "Name the release branch after the version")[0] == "none"


def test_number_difference_between_unrelated_decisions_is_not_flagged():
    assert candidate_relation("Allow 2 retries for the helper model",
                              "Name chapters with short noun phrases")[0] == "none"


@pytest.mark.parametrize("new,old", [
    ("Support Python 3.12 only", "Support Python 3.10 and newer"),
    ("Do not use Redis for the session cache", "Use Redis for the session cache"),
])
def test_conflicts_on_the_same_topic_are_flagged(new, old):
    assert conflict_relation(new, old)[0] == "possible-conflict"


def test_picking_a_rejected_option_is_flagged_even_with_little_overlap():
    assert conflict_relation("Use litellm for the helper model calls",
                             "Call the helper model with plain HTTP", ["litellm"])[0] == "rejected-alternative"


def test_short_rejected_options_are_ignored():
    assert conflict_relation("Use no cache", "Use a cache", ["no"])[0] != "rejected-alternative"
