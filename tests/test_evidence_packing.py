"""Evidence IDs, top-N limits, the token budget and redaction of packs. Written before the code."""

import pytest

from faultline.collectors.packing import (
    Candidate,
    build_packs,
    estimate_tokens,
    limit_examples,
    make_id,
    parse_id,
    top_n,
)
from faultline.schemas import EvidencePacks


def _cands(n: int, size: int = 10, source: str = "prometheus") -> list[Candidate]:
    return [Candidate(summary=f"item {i}", data={"v": "x" * size, "i": i}, source=source)
            for i in range(n)]


# ---- IDs ----

def test_make_and_parse_id() -> None:
    assert make_id("change", 2) == "chg-2"
    assert make_id("traffic", 1) == "trf-1"
    assert make_id("security", 10) == "sec-10"
    assert make_id("health", 3) == "hlt-3"
    assert parse_id("hlt-3") == ("health", 3)


@pytest.mark.parametrize("bad", ["chg-0", "xyz-1", "chg-", "chg1", "CHG-1", "chg-1a"])
def test_parse_id_rejects_bad(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_id(bad)


def test_make_id_rejects_zero() -> None:
    with pytest.raises(ValueError):
        make_id("change", 0)


def test_ids_sequential_per_pack_in_priority_order() -> None:
    packs = build_packs({"change": _cands(3), "health": _cands(2)}, top_n=10, max_tokens_total=6000)
    assert [i.id for i in packs.change] == ["chg-1", "chg-2", "chg-3"]
    assert [i.id for i in packs.health] == ["hlt-1", "hlt-2"]
    assert packs.change[0].summary == "item 0"
    assert packs.traffic == [] and packs.security == []


# ---- top-N ----

def test_top_n_helper() -> None:
    kept, dropped = top_n([5, 4, 3, 2, 1], 3)
    assert kept == [5, 4, 3]
    assert dropped == 2
    assert top_n([1], 3) == ([1], 0)


def test_top_n_rejects_non_positive() -> None:
    with pytest.raises(ValueError):
        top_n([1], 0)


def test_pack_keeps_top_n_and_counts_dropped() -> None:
    packs = build_packs({"traffic": _cands(15)}, top_n=10, max_tokens_total=6000)
    assert len(packs.traffic) == 10
    assert packs.dropped_counts == {"traffic": 5}
    assert packs.traffic[-1].summary == "item 9"


def test_no_dropped_counts_when_nothing_cut() -> None:
    packs = build_packs({"traffic": _cands(3)}, top_n=10, max_tokens_total=6000)
    assert packs.dropped_counts == {}


# ---- token budget ----

def test_estimate_tokens_is_monotonic_and_positive() -> None:
    small = estimate_tokens({"a": "x"})
    big = estimate_tokens({"a": "x" * 1000})
    assert 0 < small < big


def test_budget_enforced_by_dropping_lowest_priority_from_biggest_pack() -> None:
    packs = build_packs(
        {"change": _cands(10, size=400), "security": _cands(2, size=10)},
        top_n=10,
        max_tokens_total=1200,
    )
    assert estimate_tokens(packs.model_dump(mode="json")) <= 1200
    assert len(packs.security) == 2  # the small pack is untouched
    assert 0 < len(packs.change) < 10
    assert packs.change[-1].id == f"chg-{len(packs.change)}"  # tail dropped, IDs stay contiguous
    assert packs.dropped_counts["change"] == 10 - len(packs.change)


def test_budget_on_default_settings_with_full_packs() -> None:
    cands = {p: _cands(30, size=200) for p in ("change", "traffic", "security", "health")}
    packs = build_packs(cands, top_n=10, max_tokens_total=6000)  # type: ignore[arg-type]
    assert isinstance(packs, EvidencePacks)
    assert estimate_tokens(packs.model_dump(mode="json")) <= 6000
    assert sum(packs.dropped_counts.values()) >= 4 * 20


# ---- redaction and summary limits ----

def test_items_are_redacted() -> None:
    cand = Candidate(
        summary="login failures for bob@example.com",
        data={"line": "password=hunter2", "client": "172.18.5.9"},
        source="loki",
    )
    packs = build_packs({"security": [cand]}, top_n=10, max_tokens_total=6000)
    item = packs.security[0]
    assert "bob@example.com" not in item.summary
    assert item.data["line"] == "password=[REDACTED:secret]"
    assert item.data["client"] == "172.18.5.9"


def test_long_summary_truncated_to_schema_limit() -> None:
    cand = Candidate(summary="s" * 500, data={}, source="github")
    item = build_packs({"change": [cand]}, top_n=10, max_tokens_total=6000).change[0]
    assert len(item.summary) <= 200
    assert item.summary.endswith("...")


# ---- log example lines ----

def test_limit_examples_caps_count_length_and_redacts() -> None:
    lines = [f"error {i} for a@b.io " + "z" * 500 for i in range(10)]
    out = limit_examples(lines, max_examples=3, max_chars=120)
    assert len(out) == 3
    assert all(len(line) <= 120 for line in out)
    assert all("a@b.io" not in line for line in out)
