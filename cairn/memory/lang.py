"""Language-aware text helpers for decision matching.

Everything that compares decision texts (duplicate and conflict candidates,
keyword extraction, decision-language detection) goes through here, so one
place decides what a "word" and a "negation" are. Pure regex and word lists:
no model, no network.

Covered: English, Turkish, French, Spanish, German, Portuguese, Italian,
Russian, Chinese and Japanese. Chinese and Japanese have no spaces between
words, so runs of CJK characters are split into overlapping character pairs
(bigrams), the usual dictionary-free approach for these scripts.
"""
from __future__ import annotations

import re

# A word: letters or digits in any script, plus the path-ish characters that
# appear in decisions ("src/api", "v1.2", "snake_case"). CJK is handled apart.
_WORD_RE = re.compile(r"[^\W_][\w./-]*", re.UNICODE)
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]+")
NUMBER_RE = re.compile(r"\b(?:v?\d+(?:\.\d+){0,2}|\d+)\b", re.IGNORECASE)

# Words that flip a decision's meaning. Short ones ("no", "ne", "не") count
# here even though matching ignores words under three letters.
NEGATION_WORDS = {
    # en
    "not", "never", "without", "no", "avoid", "instead", "don't", "dont", "stop",
    # tr
    "değil", "asla", "hiç", "hiçbir", "yerine", "olmadan", "kaçın", "kaçının", "yok",
    # fr
    "ne", "pas", "jamais", "sans", "éviter", "évitez", "plutôt",
    # es
    "nunca", "sin", "evitar", "evite", "jamás", "en vez",
    # de
    "nicht", "nie", "niemals", "ohne", "kein", "keine", "keinen", "vermeiden", "statt", "anstatt",
    # pt
    "não", "nao", "sem", "evitar",
    # it
    "non", "mai", "senza", "evitare",
    # ru
    "не", "никогда", "без", "нельзя", "избегать", "вместо",
}
# Turkish negates inside the verb: kullanma, kullanmayalım, kullanmamalı, olmaz.
_TR_NEGATED_VERB = re.compile(r"\w+(?:ma|me)(?:yalım|yelim|yın|yin|yınız|yiniz|malı|meli|z|sın|sin)\b")
CJK_NEGATIONS = ("不要", "不再", "不用", "不", "别", "禁止", "避免", "没有", "无需", "勿", "しない", "ない", "禁止", "避ける")

STOPWORDS = {
    # en
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "have", "has",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "this", "that", "with", "from", "for", "and", "or", "but", "in", "on",
    "at", "to", "of", "not", "what",
    "how", "why", "when", "where", "which", "who", "can", "just", "also",
    # tr
    "ve", "ile", "bir", "bu", "şu", "için", "olarak", "gibi", "daha", "çok", "da", "de", "ki", "mi",
    "olan", "her", "ama", "veya",
    # fr
    "les", "des", "une", "pour", "avec", "dans", "est", "sur", "par", "que", "qui", "aux", "du",
    # es / pt / it
    "los", "las", "una", "para", "con", "por", "que", "del", "uma", "com", "dos", "das", "per", "che", "della",
    # de
    "der", "die", "das", "und", "mit", "für", "ist", "ein", "eine", "den", "dem", "von", "auf",
    # ru
    "и", "в", "на", "для", "что", "это", "как", "по", "из", "с",
}


def _cjk_bigrams(text: str) -> list[str]:
    out = []
    for run in _CJK_RE.findall(text):
        out += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
    return out


def words(text: str) -> set[str]:
    """Comparable tokens: words of three or more letters in any script, plus
    CJK character bigrams."""
    low = (text or "").casefold()
    found = {w for w in _WORD_RE.findall(_CJK_RE.sub(" ", low)) if len(w) >= 3}
    return found | set(_cjk_bigrams(low))


def negation_markers(text: str) -> set[str]:
    """Which negations a text uses. Two texts that differ here differ in
    meaning even when their other words match."""
    low = (text or "").casefold()
    plain = set(_WORD_RE.findall(low)) | set(re.findall(r"\w+'\w+", low))
    found = plain & NEGATION_WORDS
    found |= {f"tr:{m.group(0)}" for m in _TR_NEGATED_VERB.finditer(low)}
    found |= {neg for neg in CJK_NEGATIONS if neg in low}
    return found


def negation_differs(a: str, b: str) -> bool:
    """True when one text negates what the other states. Besides differing
    negation words, catches the Turkish bare negative imperative, which is the
    plain verb plus -ma/-me ("kullan" vs "kullanma") and too ambiguous to spot
    in one text alone ("firma", "forma")."""
    if negation_markers(a) != negation_markers(b):
        return True
    wa, wb = set(_WORD_RE.findall(a.casefold())), set(_WORD_RE.findall(b.casefold()))
    for x, other in ((wa, wb), (wb, wa)):
        for w in x - other:
            if len(w) > 4 and w[-2:] in ("ma", "me") and w[:-2] in other:
                return True
    return False


def is_negated(text: str) -> bool:
    return bool(negation_markers(text))


def keywords(text: str, limit: int = 25) -> list[str]:
    """Ordered, de-duplicated keywords for storage and recall."""
    low = (text or "").lower()
    out = []
    for w in _CJK_RE.sub(" ", low).split():
        w = w.strip(".,!?;:\"'()[]{}«»“”„‘’")
        if len(w) >= 3 and w not in STOPWORDS:
            out.append(w)
    out += _cjk_bigrams(low)
    return list(dict.fromkeys(out))[:limit]


# Phrases that usually mean a decision was just made, per language. A false
# positive costs one reminder line; a miss costs a lost decision, so these
# lean toward recall.
DECISION_PHRASES = {
    "en": r"from now on|going forward|we(?:'ve| have)? (?:agreed|decided)|(?:the )?team (?:agreed|decided)|"
          r"in (?:today'?s |the )?(?:meeting|standup)|switch(?:ing|ed)? (?:to|from|away from)|"
          r"migrat(?:e|ing|ed) (?:to|away from)|moving to|stop(?:ping|ped)? using|no longer (?:use|using)|"
          r"deprecat(?:e|ing|ed)|let'?s use|we'?ll use",
    "tr": r"bundan (?:sonra|böyle)|artık \w+ (?:kullan|yap|kullanmay)\w*|karar (?:verdik|verdim|aldık|aldım|verelim)|anlaştık|"
          r"kullanalım|kullanmayalım|kullanmayacağız|kullanacağız|geçelim|geçiyoruz|vazgeçtik|vazgeçelim|"
          r"yerine \w+ kullan",
    "fr": r"à partir de maintenant|désormais|dorénavant|(?:on a|nous avons) décidé|on part sur|"
          r"utilisons|passons à|on abandonne|n'utilisons plus",
    "es": r"a partir de ahora|de ahora en adelante|(?:hemos|habíamos) decidido|decidimos|usemos|"
          r"cambiemos a|ya no usamos|dejamos de usar",
    "de": r"ab jetzt|von nun an|ab sofort|wir haben (?:entschieden|beschlossen)|lass(?: uns)? \w+ nutzen|"
          r"wir wechseln zu|nicht mehr verwenden|wir nehmen",
    "pt": r"a partir de agora|decidimos|vamos usar|não vamos mais usar|mudamos para",
    "it": r"d'ora in poi|da ora in poi|abbiamo deciso|usiamo|passiamo a|non usiamo più",
    "ru": r"с этого момента|отныне|мы решили|давайте использовать|переходим на|больше не используем",
    "zh": r"从现在开始|以后都|今后|我们决定|决定了|改用|换成|不再使用|统一使用|就用",
    "ja": r"今後は|これからは|決定しました|に決めました|に変更します|を使うことにし|使わないことにし",
}
_SPACED = "|".join(f"(?:{p})" for k, p in DECISION_PHRASES.items() if k not in ("zh", "ja"))
DECISION_SIGNALS = re.compile(
    rf"(?<!\w)(?:{_SPACED})(?!\w)|{DECISION_PHRASES['zh']}|{DECISION_PHRASES['ja']}",
    re.IGNORECASE,
)


# A short reply that accepts what the assistant just proposed. Whole-message
# match on purpose: "tamam" alone is an answer, "tamam ama bir sorun var" is not.
_AFFIRM_WORDS = (
    "ok", "okay", "yes", "yep", "yeah", "sure", "agreed", "go ahead", "do it", "sounds good", "lets do it",
    "let's do it", "go with it", "go with that", "fine", "perfect", "approved", "ship it",
    "tamam", "evet", "olur", "peki", "uygun", "onaylıyorum", "onaylandı", "devam", "devam et", "öyle yapalım",
    "böyle yapalım", "yap", "olsun",
    "oui", "d'accord", "ça marche", "parfait", "vas-y",
    "sí", "si", "vale", "de acuerdo", "perfecto", "adelante", "hazlo",
    "ja", "einverstanden", "klar", "gut", "machen wir", "mach das",
    "sim", "certo", "combinado", "feito",
    "va bene", "d'accordo", "perfetto", "procedi",
    "да", "хорошо", "ок", "согласен", "согласна", "давай", "делай",
    "好", "好的", "好吧", "可以", "行", "没问题", "同意", "就这样", "就这么办",
    "はい", "いいよ", "いいです", "了解", "お願いします", "それで", "そうしよう", "賛成",
)
_AFFIRM_ALT = "|".join(sorted(map(re.escape, _AFFIRM_WORDS), key=len, reverse=True))
_AFFIRM_RE = re.compile(rf"^(?:{_AFFIRM_ALT})(?:\s+(?:{_AFFIRM_ALT}|[\w']{{1,12}})){{0,5}}$", re.IGNORECASE)
_TRIM = " \t\r\n.!,;:~-…。！，"


def is_affirmation(text: str) -> bool:
    """True for a short message that just says yes to the previous turn."""
    cleaned = re.sub(r"[,;:!.、，]+", " ", (text or "").strip(_TRIM)).strip()
    return bool(cleaned) and len(cleaned) <= 60 and bool(_AFFIRM_RE.match(cleaned))


# What a proposal from the assistant looks like: a question, or a "we could".
PROPOSAL_RE = re.compile(
    r"[?？]\s*$|[?？][^.\n]{0,40}$|"
    r"\b(?:should we|shall we|shall i|do you want|would you like|i(?:'d| would) (?:suggest|recommend)|"
    r"i suggest|i recommend|my recommendation|we could|"
    r"önerim|ister misin|yapalım mı|kullanalım mı|gidelim mi|"
    r"je propose|je recommande|voulez-vous|on pourrait|"
    r"propongo|recomiendo|quieres que|podríamos|"
    r"ich schlage vor|ich empfehle|sollen wir|möchtest du)\b|"
    r"我建议|建议|要不要|你觉得|おすすめ|どうしますか|にしましょうか",
    re.IGNORECASE,
)
