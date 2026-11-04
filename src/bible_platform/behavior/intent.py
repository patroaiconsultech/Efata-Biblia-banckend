"""Small, auditable intent resolver for the deterministic guide.

This does NOT claim model understanding and does not store inferred religious identity.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from bible_platform.content.catalog import works

Intent = Literal[
    "SCRIPTURE_READING",
    "SCRIPTURE_EXPLANATION",
    "THEMATIC_STUDY",
    "PRAYER_ASSISTANCE",
    "HISTORICAL_RESEARCH",
    "TRADITION_COMPARISON",
    "PERSONAL_REFLECTION",
    "PERSONAL_PROPHECY",
    "PROFESSIONAL_BOUNDARY",
    "COERCION_GUARD",
    "CRISIS_SUPPORT",
    "SPIRITUAL_GUILT_SUPPORT",
    "FAITH_DOUBT",
]
Mode = Literal["SCRIPTURE_EXPLANATION", "THEMATIC_STUDY", "PERSONAL_REFLECTION", "PRAYER_ASSISTANCE"]

@dataclass(frozen=True)
class IntentDecision:
    intent: Intent
    mode: str
    policy_ids: tuple[str, ...]
    confidence: Literal["HIGH", "MEDIUM"] = "HIGH"

def normalize(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))

def _has(value: str, expression: str) -> bool:
    return re.search(expression, value) is not None

def resolve_intent(content: str, requested_mode: Mode) -> IntentDecision:
    msg = normalize(content).strip()
    no_prayer = _has(msg, r"\b(nao quero|sem|evite|dispenso)\s+(uma\s+)?(oracao|orar)\b")
    if _has(msg, r"\b(quero (me matar|morrer|acabar com minha vida)|vou me matar|pensando em suicidio|me suicidar)\b"):
        return IntentDecision("CRISIS_SUPPORT", "SAFETY_SUPPORT", ("POL-IMMEDIATE-SAFETY",))
    if _has(msg, r"\b(deus (esta|ta) me castigando|deus (me )?castigou|minha fe e fraca|sofro porque pequei)\b"):
        return IntentDecision("SPIRITUAL_GUILT_SUPPORT", "SAFETY_SUPPORT", ("POL-NO-SPIRITUAL-BLAME",))
    if _has(msg, r"\b(estou perdendo a fe|e errado duvidar|nao sei (mais )?se acredito)\b"):
        return IntentDecision("FAITH_DOUBT", "PERSONAL_REFLECTION", ("POL-NO-COERCION",))
    if _has(msg, r"\b(parar|abandona(r|ndo)|interromper)\b.{0,45}\b(tratamento|remedio|medicamento|terapia)\b"):
        return IntentDecision("PROFESSIONAL_BOUNDARY", "SAFETY_SUPPORT", ("POL-PROFESSIONAL-BOUNDARY",))
    if _has(msg, r"\b(deus (quer|mandou)( que)?|biblia (manda|ordena))\b") and _has(msg, r"\b(ela|ele|esposa|marido|funcionario|filho|obedec(a|er)|assust)\b"):
        return IntentDecision("COERCION_GUARD", "SAFETY_SUPPORT", ("POL-NO-COERCION",))
    if _has(msg, r"\b(deus (me |te )?(mandou|revelou|prometeu|disse)|deus quer que|vontade de deus|profetiz(e|a|ar)|preveja meu futuro|sinal de deus)\b") and _has(msg, r"\b(mim|minha|meu|vou|futuro|devo|decisao|vida|casamento|emprego|quer)\b"):
        return IntentDecision("PERSONAL_PROPHECY", "SAFETY_SUPPORT", ("POL-NO-DIVINE-AUTHORITY", "POL-NO-PROPHECY"))
    if _has(msg, r"\b(nag hammadi|evangelho de tome|mar morto|qumran|manuscrito[s]?\b)"):
        return IntentDecision("HISTORICAL_RESEARCH", "HISTORICAL_RESEARCH", ("POL-HISTORICAL-CORPUS-LABEL", "POL-SOURCE-REQUIRED"))
    if _has(msg, r"\b(compar(e|ar)|diferenca[s]? entre|catolic[oa]s? (e|ou) protestant|quantos livros)\b") and _has(msg, r"\b(religi|catolic|protestant|ortodox|biblia|canon|tradic)\w*"):
        return IntentDecision("TRADITION_COMPARISON", "COMPARATIVE_TRADITIONS", ("POL-DENOMINATIONAL-RESPECT", "POL-SOURCE-REQUIRED"))
    if not no_prayer and (
        requested_mode == "PRAYER_ASSISTANCE"
        or _has(msg, r"\b(fa(c|ç)a (uma )?oracao|ore por|quero orar|pode orar|me ajude a orar)\b")
    ):
        return IntentDecision("PRAYER_ASSISTANCE", "PRAYER_ASSISTANCE", ("POL-PRAYER-OPTIONAL", "POL-NO-PROPHECY"))
    if _has(msg, r"\b(apenas (o )?texto|somente (o )?texto|so (o )?texto|sem comentario|leia|mostre)\b"):
        return IntentDecision("SCRIPTURE_READING", "READING", ("POL-SCRIPTURE-ACCURACY", "POL-SOURCE-REQUIRED"))
    if requested_mode == "PERSONAL_REFLECTION":
        return IntentDecision("PERSONAL_REFLECTION", "PERSONAL_REFLECTION", ("POL-NO-DIVINE-AUTHORITY",))
    if requested_mode == "THEMATIC_STUDY":
        return IntentDecision("THEMATIC_STUDY", "THEMATIC_STUDY", ("POL-SOURCE-REQUIRED",))
    if _has(msg, r"\b(como aplicar|o que (deus )?quer de mim|estou com medo|estou sofrendo)\b"):
        return IntentDecision("PERSONAL_REFLECTION", "PERSONAL_REFLECTION", ("POL-NO-DIVINE-AUTHORITY",))
    return IntentDecision("SCRIPTURE_EXPLANATION", "SCRIPTURE_EXPLANATION", ("POL-SCRIPTURE-ACCURACY", "POL-SOURCE-REQUIRED"), "MEDIUM")

_ALIASES: dict[str, str] = {
    "1 joao": "1joao", "2 joao": "2joao", "3 joao": "3joao",
    "gn": "gen", "genesis": "gen", "ex": "exod", "exodo": "exod",
    "sl": "sal", "salmo": "sal", "salmos": "sal",
    "mt": "mat", "mateus": "mat", "mc": "mar", "marcos": "mar",
    "lc": "luc", "lucas": "luc", "joao": "joao",
}

def parse_reference(message: str) -> tuple[str, int, int] | None:
    """Matches book + chapter:verse, without mistaking 1 John for John's Gospel.

    Text outside the documented corpus and chapter-only requests are not guessed.
    """
    text = normalize(message)
    book_names = {normalize(w.title): w.slug for w in works()}
    book_names.update(_ALIASES)
    matches: list[tuple[int, int, str, int, int]] = []
    for name, slug in book_names.items():
        pat = rf"(?<![a-z0-9]){re.escape(name)}\s*[.,]?\s*(\d{{1,3}})\s*[:.]\s*(\d{{1,3}})(?!\d)"
        for m in re.finditer(pat, text):
            # Prefer longer matching book names at the same passage ending.
            matches.append((m.start(), -len(name), slug, int(m.group(1)), int(m.group(2))))
    if not matches:
        return None
    # First book mention; for same location, prefer longest alias.
    matches.sort()
    _, _, slug, chapter, verse = matches[0]
    if chapter <= 0 or verse <= 0:
        return None
    return f"work_{slug}", chapter, verse
