from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .config import TargetConfig
from .http_client import HttpClient


class ConceptSearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_id: str
    pt: str | None = None
    fsn: str | None = None
    active: bool | None = None
    matched_term: str | None = None
    definition_status: str | None = None
    module_id: str | None = None
    semantic_tag: str | None = None


class ConceptSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str
    branch: str
    limit: int
    total_elements: int | None = None
    returned: int
    hits: list[ConceptSearchHit] = Field(default_factory=list)


class ConceptDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_id: str
    pt: str | None = None
    fsn: str | None = None
    semantic_tag: str | None = None
    active: bool | None = None
    definition_status: str | None = None
    module_id: str | None = None
    effective_time: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    raw_description_count: int | None = None


class SnowstormNativeService:
    MIN_SEARCH_TERM_LENGTH = 3

    def __init__(self, target: TargetConfig, *, client: HttpClient | None = None) -> None:
        self.target = target
        self._own_client = client is None
        self.client = client or HttpClient(target)

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def __enter__(self) -> "SnowstormNativeService":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def search_concepts(
        self,
        *,
        term: str,
        branch: str = "MAIN",
        limit: int = 10,
        active_only: bool = True,
    ) -> ConceptSearchResult:
        searchable_len = _searchable_term_length(term)
        if searchable_len < self.MIN_SEARCH_TERM_LENGTH:
            raise ValueError(
                "Snowstorm native concept search requires a term of at least "
                f"{self.MIN_SEARCH_TERM_LENGTH} searchable characters "
                f"(letters/digits) after normalization; got {searchable_len} for {term!r}. "
                "Use a longer term/phrase (for acronyms, include context) or another tool."
            )
        branch_path = branch.strip("/") or "MAIN"
        url = f"{self.target.base_url}/browser/{branch_path}/descriptions"
        raw_limit = max(1, min(limit * 4, 100))  # over-fetch then dedupe/filter
        params = {
            "term": term,
            "active": "true" if active_only else "false",
            "limit": str(raw_limit),
        }
        data = self.client.request("GET", url, params=params, expect_json=True)
        items = data.get("items", [])
        query_norm = _normalize_query(term)
        scored_hits: list[tuple[tuple[int, int, int], ConceptSearchHit]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            concept = item.get("concept")
            if not isinstance(concept, dict):
                continue
            concept_id = concept.get("conceptId") or concept.get("id")
            if not isinstance(concept_id, str):
                continue
            if concept_id in seen:
                continue
            active = concept.get("active")
            if active_only and active is False:
                continue
            pt = (concept.get("pt") or {}).get("term") if isinstance(concept.get("pt"), dict) else None
            fsn = (concept.get("fsn") or {}).get("term") if isinstance(concept.get("fsn"), dict) else None
            hit = ConceptSearchHit(
                concept_id=concept_id,
                pt=pt if isinstance(pt, str) else None,
                fsn=fsn if isinstance(fsn, str) else None,
                active=active if isinstance(active, bool) else None,
                matched_term=item.get("term") if isinstance(item.get("term"), str) else None,
                definition_status=(
                    concept.get("definitionStatus")
                    if isinstance(concept.get("definitionStatus"), str)
                    else None
                ),
                module_id=concept.get("moduleId") if isinstance(concept.get("moduleId"), str) else None,
                semantic_tag=_semantic_tag_from_fsn(fsn if isinstance(fsn, str) else None),
            )
            scored_hits.append((_search_score(query_norm, hit), hit))
            seen.add(concept_id)
            if len(scored_hits) >= raw_limit:
                break
        scored_hits.sort(key=lambda x: x[0], reverse=True)
        hits = [hit for _, hit in scored_hits[:limit]]
        total_elements = data.get("totalElements")
        return ConceptSearchResult(
            term=term,
            branch=branch_path,
            limit=limit,
            total_elements=total_elements if isinstance(total_elements, int) else None,
            returned=len(hits),
            hits=hits,
        )

    def get_concept(
        self,
        *,
        concept_id: str,
        branch: str = "MAIN",
        include_synonyms: bool = True,
        max_synonyms: int = 15,
    ) -> ConceptDetail:
        branch_path = branch.strip("/") or "MAIN"
        url = f"{self.target.base_url}/browser/{branch_path}/concepts/{concept_id}"
        data = self.client.request("GET", url, expect_json=True)
        fsn = _nested_term(data.get("fsn"))
        pt = _nested_term(data.get("pt"))
        synonyms: list[str] = []
        descriptions = data.get("descriptions")
        if include_synonyms and isinstance(descriptions, list):
            seen: set[str] = set()
            for d in descriptions:
                if not isinstance(d, dict):
                    continue
                if d.get("active") is not True:
                    continue
                if d.get("type") not in {"SYNONYM", "PT"}:
                    continue
                term = d.get("term")
                if not isinstance(term, str):
                    continue
                key = term.casefold()
                if key in seen:
                    continue
                seen.add(key)
                synonyms.append(term)
                if len(synonyms) >= max_synonyms:
                    break
        return ConceptDetail(
            concept_id=str(data.get("conceptId") or concept_id),
            pt=pt,
            fsn=fsn,
            semantic_tag=_semantic_tag_from_fsn(fsn),
            active=data.get("active") if isinstance(data.get("active"), bool) else None,
            definition_status=(
                data.get("definitionStatus") if isinstance(data.get("definitionStatus"), str) else None
            ),
            module_id=data.get("moduleId") if isinstance(data.get("moduleId"), str) else None,
            effective_time=data.get("effectiveTime") if isinstance(data.get("effectiveTime"), str) else None,
            synonyms=synonyms,
            raw_description_count=len(descriptions) if isinstance(descriptions, list) else None,
        )


def _nested_term(obj: Any) -> str | None:
    if isinstance(obj, dict):
        term = obj.get("term")
        if isinstance(term, str):
            return term
    return None


def _semantic_tag_from_fsn(fsn: str | None) -> str | None:
    if not fsn:
        return None
    if "(" not in fsn or not fsn.endswith(")"):
        return None
    return fsn.rsplit("(", 1)[-1][:-1]


def _normalize_query(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(text.casefold().split())


def _searchable_term_length(text: str | None) -> int:
    if not text:
        return 0
    return len(re.sub(r"[^0-9a-z]+", "", text.casefold()))


def _text_without_semantic_tag(text: str | None) -> str:
    if not text:
        return ""
    out = text.casefold().strip()
    if out.endswith(")") and "(" in out:
        out = out.rsplit("(", 1)[0].strip()
    return " ".join(out.split())


def _search_score(query_norm: str, hit: ConceptSearchHit) -> tuple[int, int, int]:
    pt = _normalize_query(hit.pt)
    matched = _normalize_query(hit.matched_term)
    fsn_core = _text_without_semantic_tag(hit.fsn)
    texts = [pt, matched, fsn_core]
    exact = any(query_norm and query_norm == t for t in texts)
    prefix = any(query_norm and t.startswith(query_norm) for t in texts if t)
    contains = any(query_norm and query_norm in t for t in texts)
    short_bonus = max(0, 200 - len(matched)) if matched else 0
    return (int(exact) * 1000 + int(prefix) * 500 + int(contains) * 200, int(hit.active is True), short_bonus)
