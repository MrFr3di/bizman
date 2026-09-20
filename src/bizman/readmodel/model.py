from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
import unicodedata


_MAX_QUERY_LENGTH = 512
_MAX_SEARCH_LIMIT = 50
_WHITESPACE_RE = re.compile(r"\s+")


class RefKind(StrEnum):
    ACTION = "action"
    PRODUCT = "product"
    CITY = "city"
    COMPANY = "company"
    UNIT = "unit"
    ENDPOINT = "endpoint"
    OPERATION = "operation"
    FORM = "form"
    WIKI = "wiki"


_REF_PREFIX = {
    RefKind.ACTION: "bm.action.",
    RefKind.PRODUCT: "bm.product.",
    RefKind.CITY: "bm.city.",
    RefKind.COMPANY: "bm.company.",
    RefKind.UNIT: "bm.unit.",
    RefKind.ENDPOINT: "bm.endpoint.",
    RefKind.OPERATION: "bm.operation.",
    RefKind.FORM: "bm.form.",
    RefKind.WIKI: "bm.wiki.",
}


class MatchKind(StrEnum):
    EXACT_REF = "exact_ref"
    EXACT_ALIAS = "exact_alias"
    EXACT_TITLE = "exact_title"
    FULL_TEXT = "full_text"


def normalize_search_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("search text must be a string")
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _WHITESPACE_RE.sub(" ", normalized).strip()


@dataclass(frozen=True, slots=True)
class KnowledgeRecord:
    ref: str
    kind: RefKind
    title: str
    aliases: tuple[str, ...]
    body: str
    evidence_refs: tuple[str, ...]
    source_dataset: str

    def __post_init__(self) -> None:
        if not isinstance(self.ref, str) or not self.ref:
            raise TypeError("ref must be a non-empty string")
        if not isinstance(self.kind, RefKind):
            raise TypeError("kind must be RefKind")
        if not self.ref.startswith(_REF_PREFIX[self.kind]):
            raise ValueError(
                f"ref {self.ref!r} does not match kind {self.kind.value!r}"
            )
        if not isinstance(self.title, str) or not self.title.strip():
            raise TypeError("title must be a non-empty string")
        if not isinstance(self.body, str):
            raise TypeError("body must be a string")
        if not isinstance(self.source_dataset, str) or not self.source_dataset:
            raise TypeError("source_dataset must be a non-empty string")

        aliases = tuple(self.aliases)
        evidence_refs = tuple(self.evidence_refs)
        if not all(isinstance(value, str) and value.strip() for value in aliases):
            raise TypeError("aliases must contain only non-empty strings")
        if not all(isinstance(value, str) and value for value in evidence_refs):
            raise TypeError("evidence_refs must contain only non-empty strings")

        normalized_aliases: dict[str, str] = {}
        for alias in aliases:
            normalized = normalize_search_text(alias)
            if not normalized:
                continue
            normalized_aliases.setdefault(normalized, alias.strip())

        object.__setattr__(
            self,
            "aliases",
            tuple(normalized_aliases[key] for key in sorted(normalized_aliases)),
        )
        object.__setattr__(self, "evidence_refs", tuple(dict.fromkeys(evidence_refs)))


@dataclass(frozen=True, slots=True)
class SearchQuery:
    text: str
    limit: int = 20
    kinds: tuple[RefKind, ...] = ()

    def __post_init__(self) -> None:
        normalized = normalize_search_text(self.text)
        if not normalized:
            raise ValueError("search text must not be empty")
        if len(self.text) > _MAX_QUERY_LENGTH:
            raise ValueError(f"search text exceeds {_MAX_QUERY_LENGTH} characters")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise TypeError("limit must be an integer")
        if not 1 <= self.limit <= _MAX_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {_MAX_SEARCH_LIMIT}")

        kinds = tuple(self.kinds)
        if not all(isinstance(kind, RefKind) for kind in kinds):
            raise TypeError("kinds must contain only RefKind values")

        object.__setattr__(self, "text", self.text.strip())
        object.__setattr__(self, "kinds", tuple(dict.fromkeys(kinds)))


@dataclass(frozen=True, slots=True)
class SearchHit:
    ref: str
    kind: RefKind
    title: str
    match_kind: MatchKind
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        evidence_refs = tuple(self.evidence_refs)
        if not all(isinstance(value, str) and value for value in evidence_refs):
            raise TypeError("evidence_refs must contain only non-empty strings")
        object.__setattr__(self, "evidence_refs", evidence_refs)


__all__ = [
    "KnowledgeRecord",
    "MatchKind",
    "RefKind",
    "SearchHit",
    "SearchQuery",
    "normalize_search_text",
]