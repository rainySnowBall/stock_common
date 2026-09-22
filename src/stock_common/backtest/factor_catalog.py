"""Factor catalog parsed from the local Tushare factor map."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class FactorCatalogEntry:
    factor_name: str
    asset_type: str
    factor_type: str
    factor_desc: str


@dataclass(frozen=True)
class FactorKeywordMapping:
    phrase: str
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class FactorCandidateMatch:
    factor_name: str
    index: int
    rank: int
    score: int
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class FactorCatalog:
    entries: dict[str, FactorCatalogEntry]
    keyword_mappings: tuple[FactorKeywordMapping, ...]

    def has_factor(self, factor_name: str) -> bool:
        return factor_name in self.entries

    def entry(self, factor_name: str) -> FactorCatalogEntry | None:
        return self.entries.get(factor_name)

    def aliases(self, factor_name: str) -> tuple[str, ...]:
        aliases = [factor_name]
        for mapping in self.keyword_mappings:
            if factor_name in mapping.candidates:
                aliases.append(mapping.phrase)
                if mapping.candidates and mapping.candidates[0] == factor_name:
                    aliases.extend(_mapping_terms(mapping.phrase))
        return tuple(dict.fromkeys(aliases))


_CODE_RE = re.compile(r"`([^`]+)`")
_ALIAS_SPLIT_RE = re.compile(r"\s*(?:/|／|、|,|，|;|；|\||或)\s*")
_LATIN_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_SOFT_MATCH_STOPWORDS = {
    "因子",
    "股票",
    "回测",
    "策略",
    "选择",
    "排序",
    "最高",
    "最低",
    "较高",
    "较低",
    "过去",
    "收益",
    "收益率",
    "因子值",
    "量因",
    "量因子",
    "股票池",
}


@lru_cache(maxsize=1)
def load_factor_catalog() -> FactorCatalog:
    path = _factor_map_path()
    if not path.exists():
        return FactorCatalog(entries={}, keyword_mappings=())
    return parse_factor_map(path.read_text(encoding="utf-8"))


def parse_factor_map(content: str) -> FactorCatalog:
    entries: dict[str, FactorCatalogEntry] = {}
    mappings: list[FactorKeywordMapping] = []
    section = ""
    factor_type = ""

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            section = line
            factor_type = ""
            continue
        if section.startswith("## 6.") and line.startswith("### "):
            factor_type = line.removeprefix("### ").strip()
            continue

        if section.startswith("## 4."):
            mapping = _parse_keyword_mapping(line)
            if mapping is not None:
                mappings.append(mapping)
            continue

        if section.startswith("## 6.") and factor_type:
            entry = _parse_factor_entry(line, factor_type)
            if entry is not None:
                entries[entry.factor_name] = entry

    return FactorCatalog(entries=entries, keyword_mappings=tuple(mappings))


def factor_exists(factor_name: str) -> bool:
    return load_factor_catalog().has_factor(factor_name)


def factor_display_name(factor_name: str) -> str:
    entry = load_factor_catalog().entry(factor_name)
    if entry is None:
        return factor_name
    return factor_name


def factor_aliases(factor_name: str) -> tuple[str, ...]:
    return load_factor_catalog().aliases(factor_name)


def search_factor_candidates(text: str, *, limit: int = 8) -> tuple[str, ...]:
    """Return likely catalog factor_names mentioned by name, alias, or description."""

    return tuple(match.factor_name for match in search_factor_matches(text, limit=limit))


def search_factor_matches(text: str, *, limit: int = 8) -> tuple[FactorCandidateMatch, ...]:
    """Return factor matches plus the terms that made each candidate parseable."""

    catalog = load_factor_catalog()
    normalized = _normalize_query(text)
    query_terms = _query_terms(text)
    matches: list[FactorCandidateMatch] = []

    for factor_name in catalog.entries:
        index = normalized.find(_normalize_query(factor_name))
        if index >= 0:
            matches.append(
                FactorCandidateMatch(
                    factor_name=factor_name,
                    index=index,
                    rank=0,
                    score=100,
                    matched_terms=(factor_name,),
                )
            )

    for mapping in catalog.keyword_mappings:
        for term in _mapping_terms(mapping.phrase):
            index = normalized.find(_normalize_query(term))
            if index < 0:
                continue
            for rank, factor_name in enumerate(mapping.candidates):
                if catalog.has_factor(factor_name):
                    matches.append(
                        FactorCandidateMatch(
                            factor_name=factor_name,
                            index=index,
                            rank=rank + 1,
                            score=80 - rank,
                            matched_terms=(term,),
                        )
                    )

    for entry in catalog.entries.values():
        score, terms = _entry_match_score(entry, normalized, query_terms)
        if score > 0:
            index = min(
                (
                    normalized.find(_normalize_query(term))
                    for term in terms
                    if normalized.find(_normalize_query(term)) >= 0
                ),
                default=len(normalized) + 1,
            )
            matches.append(
                FactorCandidateMatch(
                    factor_name=entry.factor_name,
                    index=index,
                    rank=100,
                    score=score,
                    matched_terms=terms,
                )
            )

    ordered = []
    seen = set()
    for match in sorted(matches, key=lambda item: (item.index, item.rank, -item.score, _factor_name_preference(item.factor_name), item.factor_name)):
        if match.factor_name in seen:
            continue
        seen.add(match.factor_name)
        ordered.append(match)
        if len(ordered) >= limit:
            break
    return tuple(ordered)


def factor_catalog_prompt() -> str:
    catalog = load_factor_catalog()
    mapping_lines = [
        f"- {item.phrase} => {', '.join(item.candidates)}"
        for item in catalog.keyword_mappings
    ]
    entry_lines = [
        f"- [{entry.factor_type}] {entry.factor_name}: {_compact_desc(entry.factor_desc)}"
        for entry in catalog.entries.values()
    ]
    return "\n".join(
        (
            "常用中文查询词到真实 factor_name 的候选映射：",
            *mapping_lines,
            "",
            "完整可用 factor_value 因子列表，抽取时必须使用这里的 factor_name：",
            *entry_lines,
        )
    )


def _parse_keyword_mapping(line: str) -> FactorKeywordMapping | None:
    cells = _markdown_cells(line)
    if len(cells) < 2:
        return None
    phrase, candidates_text = cells[:2]
    if "用户说法" in phrase or set(phrase) <= {"-", " "}:
        return None
    candidates = tuple(_CODE_RE.findall(candidates_text))
    if not candidates:
        return None
    return FactorKeywordMapping(phrase=_strip_markdown(phrase), candidates=candidates)


def _parse_factor_entry(line: str, factor_type: str) -> FactorCatalogEntry | None:
    cells = _markdown_cells(line)
    if len(cells) < 3:
        return None
    factor_text, asset_type, factor_desc = cells[:3]
    if "factor_name" in factor_text or set(factor_text) <= {"-", " "}:
        return None
    code_match = _CODE_RE.search(factor_text)
    if code_match is None:
        return None
    return FactorCatalogEntry(
        factor_name=code_match.group(1),
        asset_type=_strip_markdown(asset_type),
        factor_type=factor_type,
        factor_desc=_strip_markdown(factor_desc),
    )


def _compact_desc(value: str, limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _strip_markdown(value: str) -> str:
    return value.replace("`", "").strip()


def _normalize_query(value: str) -> str:
    return re.sub(r"\s+", "", value.lower())


def _mapping_terms(phrase: str) -> tuple[str, ...]:
    terms = [_strip_markdown(phrase)]
    terms.extend(_ALIAS_SPLIT_RE.split(_strip_markdown(phrase)))
    return tuple(
        dict.fromkeys(
            term.strip()
            for term in terms
            if term and _is_significant_query_term(term.strip())
        )
    )


def _query_terms(text: str) -> frozenset[str]:
    normalized = text.lower()
    terms = set(_LATIN_TOKEN_RE.findall(normalized))
    for cjk_run in _CJK_RUN_RE.findall(normalized):
        if len(cjk_run) >= 2:
            terms.add(cjk_run)
        for width in (2, 3, 4):
            if len(cjk_run) <= width:
                continue
            for index in range(0, len(cjk_run) - width + 1):
                terms.add(cjk_run[index : index + width])
    return frozenset(
        term
        for term in terms
        if _is_significant_query_term(term)
    )


def _entry_match_score(
    entry: FactorCatalogEntry,
    normalized_query: str,
    query_terms: frozenset[str],
) -> tuple[int, tuple[str, ...]]:
    factor_name = _normalize_query(entry.factor_name)
    factor_type = _normalize_query(entry.factor_type)
    description = _normalize_query(entry.factor_desc)
    score = 0
    matched_terms: list[str] = []

    for token in _LATIN_TOKEN_RE.findall(factor_name):
        if _is_significant_query_term(token) and (token in query_terms or token in normalized_query):
            score += 10
            matched_terms.append(token)
    if factor_type and factor_type in normalized_query:
        score += 4
        matched_terms.append(entry.factor_type)

    for term in query_terms:
        normalized_term = _normalize_query(term)
        if len(normalized_term) < 2:
            continue
        if normalized_term in factor_name:
            score += _term_score(normalized_term) + 5
            matched_terms.append(term)
        if normalized_term in description:
            score += _term_score(normalized_term)
            matched_terms.append(term)
    if score < 5:
        return 0, ()
    terms = tuple(
        sorted(
            dict.fromkeys(matched_terms),
            key=lambda item: (-len(_normalize_query(item)), _normalize_query(item)),
        )
    )
    return score, terms


def _is_significant_query_term(term: str) -> bool:
    normalized = _normalize_query(term)
    if not normalized or normalized in _SOFT_MATCH_STOPWORDS:
        return False
    if normalized.isdigit():
        return False
    if _LATIN_TOKEN_RE.fullmatch(normalized):
        return len(normalized) >= 2 and not normalized.isdigit()
    return len(normalized) >= 2


def _term_score(term: str) -> int:
    if _LATIN_TOKEN_RE.fullmatch(term):
        return 8
    length = len(term)
    if length >= 5:
        return 10
    if length == 4:
        return 8
    if length == 3:
        return 5
    return 2


def _factor_name_preference(factor_name: str) -> int:
    if factor_name.endswith("_21d"):
        return 0
    if factor_name.endswith("_ttm"):
        return 1
    if factor_name.endswith("_q"):
        return 2
    if factor_name.endswith("_y"):
        return 3
    return 4


def _markdown_cells(line: str) -> list[str]:
    if not line.startswith("|") or not line.endswith("|"):
        return []
    return [cell.strip() for cell in line.strip("|").split("|")]


def _factor_map_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data_fetch" / "factor_map.md"
