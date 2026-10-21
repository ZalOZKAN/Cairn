"""Language-aware matching: words, negation and decision-language signals."""
from __future__ import annotations

import pytest

from cairn.memory import lang
from cairn.memory.capture import looks_like_decision
from cairn.memory.dedup import candidate_relation, conflict_relation


def test_words_keep_non_ascii_letters_and_split_cjk_into_bigrams():
    assert {"çerez", "kullanıcı", "oturumları"} <= lang.words("Kullanıcı oturumları için çerez")
    assert {"缓存", "存使"} <= lang.words("缓存使用Redis")


@pytest.mark.parametrize("a,b", [
    ("Önbellek için Redis kullan", "Önbellek için Redis kullanma"),
    ("Oturumlar için çerez kullanalım", "Oturumlar için çerez kullanmayalım"),
    ("Utiliser Redis pour le cache", "Ne pas utiliser Redis pour le cache"),
    ("Redis für den Cache verwenden", "Redis nicht für den Cache verwenden"),
    ("缓存使用Redis", "缓存不要使用Redis"),
    ("キャッシュにRedisを使う", "キャッシュにRedisを使わない"),
])
def test_negated_rewording_is_never_a_duplicate(a, b):
    assert candidate_relation(a, b)[0] == "protected-difference"


@pytest.mark.parametrize("a,b", [
    ("Yerel depolama için SQLite kullan", "Yerel depolama katmanı için SQLite kullan"),
    ("本地存储使用SQLite数据库", "本地存储层使用SQLite数据库"),
])
def test_non_english_rewordings_are_duplicates(a, b):
    assert candidate_relation(a, b)[0] == "possible-duplicate"


def test_turkish_words_ending_in_ma_alone_are_not_negation():
    assert not lang.negation_differs("Firma adını README içine yaz", "Firma adını README içine yaz")
    assert conflict_relation("Firma için SQLite kullan", "Firma için SQLite kullan")[0] == "none"


@pytest.mark.parametrize("text", [
    "Bundan sonra SQLite kullanalım",
    "Karar verdik: çerezler httpOnly olacak",
    "Désormais on utilise SQLite",
    "A partir de ahora usamos SQLite",
    "Ab jetzt nehmen wir SQLite",
    "从现在开始我们统一使用SQLite",
    "今後はSQLiteを使う",
    "We agreed to use SQLite",
])
def test_decision_language_is_detected_in_each_language(text):
    assert looks_like_decision(text)


def test_ordinary_text_is_not_a_decision():
    assert looks_like_decision("Bu fonksiyon listeyi sıralıyor") is None
    assert looks_like_decision("return x instead of y") is None
