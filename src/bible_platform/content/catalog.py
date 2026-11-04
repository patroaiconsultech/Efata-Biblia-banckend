from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from bible_platform.config import get_settings
from bible_platform.domain_models import CanonProfile, Corpus, RightsRecord, SourceRegistry, TextUnit, Translation, Work

BOOK_ORDER = [
    "gen", "exod", "lev", "num", "deut", "jos", "juiz", "rute", "1sa", "2sa", "1rs", "2rs", "1crn", "2crn", "esd", "nee", "est", "jo", "sal", "prov", "ecl", "cant", "isa", "jer", "lam", "eze", "dan", "ose", "joel", "amos", "oba", "jon", "miq", "naum", "hab", "sof", "ageu", "zac", "mal", "mat", "mar", "luc", "joao", "atos", "rom", "1cor", "2cor", "gal", "efes", "fil", "col", "1tes", "2tes", "1tim", "2tim", "tito", "flm", "heb", "tiag", "1ped", "2ped", "1joao", "2joao", "3joao", "jud", "apo",
]

TESTAMENT = {slug: ("old" if index < 39 else "new") for index, slug in enumerate(BOOK_ORDER)}


def _source_root() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "corpus" / "bible-livre" / "f4" / "geral"


def _book_file(slug: str) -> Path:
    return _source_root() / f"{slug}.txt"


def _title_from_file(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return path.stem
    for index, line in enumerate(lines):
        if line.strip() == "\\name-long" and index + 1 < len(lines):
            return lines[index + 1].strip()
    return path.stem


def _abbreviation_from_file(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return path.stem
    for index, line in enumerate(lines):
        if line.strip() == "\\abbreviation" and index + 1 < len(lines):
            return lines[index + 1].strip()
    return path.stem


def _iter_units(path: Path, work_id: str, translation_id: str) -> Iterable[TextUnit]:
    if not path.exists():
        return
    current: tuple[int, int, str] | None = None
    body: list[str] = []
    footnote = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("\\v "):
            if current is not None:
                chapter, verse, reference = current
                text = " ".join(part.strip() for part in body if part.strip()).strip()
                if text:
                    yield TextUnit(
                        text_unit_id=f"tu_{work_id}_{chapter}_{verse}",
                        work_id=work_id,
                        translation_id=translation_id,
                        chapter=chapter,
                        verse=verse,
                        reference=reference,
                        text=text,
                        source_id="bible-livre",
                        source_version=get_settings().content_source_version,
                    )
            token = line[3:].strip()
            match = re.match(r"[^.]+\.(\d+)\.(\d+)", token)
            if not match:
                current = None
                body = []
                continue
            current = (int(match.group(1)), int(match.group(2)), token)
            body = []
            footnote = False
            continue
        if line in {"\\fn", "\\x"}:
            footnote = True
            continue
        if line in {"\\*fn", "\\*x"}:
            footnote = False
            continue
        if line.startswith("\\") or footnote or current is None:
            continue
        body.append(line)
    if current is not None:
        chapter, verse, reference = current
        text = " ".join(part.strip() for part in body if part.strip()).strip()
        if text:
            yield TextUnit(
                text_unit_id=f"tu_{work_id}_{chapter}_{verse}",
                work_id=work_id,
                translation_id=translation_id,
                chapter=chapter,
                verse=verse,
                reference=reference,
                text=text,
                source_id="bible-livre",
                source_version=get_settings().content_source_version,
            )


def source_registry() -> SourceRegistry:
    return SourceRegistry(
        source_id="bible-livre",
        title="Bíblia Livre (BLIVRE)",
        url="https://github.com/blivre/BibliaLivre",
        version=get_settings().content_source_version,
        retrieved_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attribution="Bíblia Livre (BLIVRE); autores e licença CC BY 3.0 Brasil conforme LICENCA.md.",
    )


def local_rights_record() -> RightsRecord:
    return RightsRecord(
        rights_record_id="rights_bible_livre",
        source_id="bible-livre",
        commercial_use=True,
        redistribution=True,
        modification=True,
        ai_processing=True,
        rag_ingestion=True,
        image_display=False,
        attribution_required=True,
        legal_review="pending",
    )


def translation() -> Translation:
    settings = get_settings()
    return Translation(
        translation_id="blivre-2025.1.0",
        name="Bíblia Livre",
        language="pt-BR",
        version=settings.content_source_version,
        source_id="bible-livre",
        rights_record_id="rights_bible_livre",
        status="pending",
    )


def corpus() -> Corpus:
    return Corpus(
        corpus_id="canonical_scripture",
        name="Escritura — Bíblia Livre",
        description="Corpus bíblico em português com proveniência e atribuição registradas.",
        status="active",
    )


def canon_profiles() -> tuple[CanonProfile, ...]:
    return (
        CanonProfile(profile_id="ecumenical_neutral", name="Ecumênico neutro", description="Perfil inicial que expõe diferenças sem atacar tradições."),
        CanonProfile(profile_id="protestant", name="Protestante", description="Perfil de referência protestante."),
        CanonProfile(profile_id="catholic", name="Católico", description="Perfil de referência católico; obras adicionais serão adicionadas com rights."),
        CanonProfile(profile_id="orthodox", name="Ortodoxo", description="Perfil de referência ortodoxo."),
        CanonProfile(profile_id="historical_academic", name="Histórico-acadêmico", description="Perfil para pesquisa e transmissão textual."),
    )


def works() -> tuple[Work, ...]:
    items: list[Work] = []
    for order, slug in enumerate(BOOK_ORDER, start=1):
        path = _book_file(slug)
        title = _title_from_file(path)
        abbreviation = _abbreviation_from_file(path)
        items.append(Work(
            work_id=f"work_{slug}",
            corpus_id="canonical_scripture",
            title=title,
            slug=slug,
            testament=TESTAMENT[slug],
            book_order=order,
            canonical_status={"ecumenical_neutral": "CANONICAL"},
        ))
    return tuple(items)


@lru_cache(maxsize=1)
def all_units() -> tuple[TextUnit, ...]:
    result: list[TextUnit] = []
    translation_id = translation().translation_id
    for work in works():
        result.extend(_iter_units(_book_file(work.slug), work.work_id, translation_id))
    return tuple(result)


def find_units(query: str, *, work_id: str | None = None, chapter: int | None = None) -> list[TextUnit]:
    normalized = " ".join(query.casefold().split())
    items = all_units()
    result: list[TextUnit] = []
    for item in items:
        if work_id and item.work_id != work_id:
            continue
        if chapter and item.chapter != chapter:
            continue
        if not normalized or normalized in item.text.casefold() or normalized in item.reference.casefold():
            result.append(item)
    return result[:100]


def units_for_chapter(work_id: str, chapter: int) -> list[TextUnit]:
    return [item for item in all_units() if item.work_id == work_id and item.chapter == chapter]
