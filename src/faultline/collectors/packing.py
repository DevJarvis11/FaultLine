"""Turn collector output into evidence packs: redaction, top-N limits, a token budget, IDs.

DESIGN (Evidence packs, Size limits and redaction): each pack keeps top-N items and counts
what it dropped; the four packs together stay within `evidence.max_tokens_total`. Collectors
hand over candidates in priority order (most relevant first), so limits cut from the tail.
"""

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, get_args

from pydantic import BaseModel

from faultline.redaction import redact, redact_text
from faultline.schemas import EvidenceItem, EvidencePacks, Pack

PACKS: tuple[Pack, ...] = get_args(Pack)
PREFIX: dict[Pack, str] = {"change": "chg", "traffic": "trf", "security": "sec", "health": "hlt"}
_PACK_OF = {v: k for k, v in PREFIX.items()}
_ID = re.compile(r"^(chg|trf|sec|hlt)-([1-9][0-9]*)$")

SUMMARY_MAX = 200  # EvidenceItem.summary max_length
# A conservative estimate (no local tokenizer for the remote model): JSON with numbers and
# punctuation tokenizes densely, so assume 3 characters per token rather than 4.
CHARS_PER_TOKEN = 3


class Candidate(BaseModel):
    """One collector finding before redaction, limits and ID assignment."""

    summary: str
    data: dict[str, Any]
    source: str


def make_id(pack: Pack, n: int) -> str:
    if n < 1:
        raise ValueError("evidence numbers start at 1")
    return f"{PREFIX[pack]}-{n}"


def parse_id(item_id: str) -> tuple[Pack, int]:
    m = _ID.match(item_id)
    if not m:
        raise ValueError(f"not an evidence ID: {item_id!r}")
    return _PACK_OF[m.group(1)], int(m.group(2))


def top_n[T](values: Sequence[T], n: int) -> tuple[list[T], int]:
    """Keep the first `n` values; return them and how many were dropped."""
    if n < 1:
        raise ValueError("top_n needs n >= 1")
    return list(values[:n]), max(len(values) - n, 0)


def estimate_tokens(obj: Any) -> int:
    text = json.dumps(obj, separators=(",", ":"), default=str)
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def limit_examples(lines: Sequence[str], max_examples: int = 3, max_chars: int = 300) -> list[str]:
    """A few redacted, shortened example log lines to go with pattern counts."""
    kept, _ = top_n(lines, max_examples)
    return [_truncate(redact_text(line), max_chars) for line in kept]


def _to_item(pack: Pack, n: int, cand: Candidate) -> EvidenceItem:
    return EvidenceItem(
        id=make_id(pack, n),
        pack=pack,
        summary=_truncate(redact_text(cand.summary), SUMMARY_MAX),
        data=redact(cand.data),
        source=cand.source,
    )


def _total(items: Mapping[Pack, list[EvidenceItem]], dropped: Mapping[Pack, int]) -> int:
    return estimate_tokens(_assemble(items, dropped).model_dump(mode="json"))


def _assemble(
    items: Mapping[Pack, list[EvidenceItem]], dropped: Mapping[Pack, int]
) -> EvidencePacks:
    return EvidencePacks(
        **{p: list(items[p]) for p in PACKS},
        dropped_counts={p: c for p, c in dropped.items() if c > 0},
    )


def build_packs(
    candidates: Mapping[Pack, Sequence[Candidate]], top_n: int, max_tokens_total: int
) -> EvidencePacks:
    """Redact, cut each pack to `top_n`, then trim the biggest pack's tail until in budget."""
    items: dict[Pack, list[EvidenceItem]] = {}
    dropped: dict[Pack, int] = {}
    for pack in PACKS:
        kept, cut = _keep_first(candidates.get(pack, []), top_n)
        items[pack] = [_to_item(pack, i, c) for i, c in enumerate(kept, start=1)]
        dropped[pack] = cut
    while _total(items, dropped) > max_tokens_total:
        nonempty = [p for p in PACKS if items[p]]
        if not nonempty:
            break
        biggest = max(nonempty, key=lambda p: estimate_tokens(
            [i.model_dump(mode="json") for i in items[p]]))
        items[biggest].pop()
        dropped[biggest] += 1
    return _assemble(items, dropped)


_keep_first = top_n  # build_packs' `top_n` parameter shadows the function
