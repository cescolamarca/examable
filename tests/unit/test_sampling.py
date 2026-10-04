from __future__ import annotations

import random
from collections import Counter

from app.services.sampling import UNTAGGED, interleave_by_topic, primary_topic


def topic(item: tuple[str, int]) -> str:
    return item[0]


def test_round_robin_spreads_a_short_sample_over_topics() -> None:
    # 10 questions on TCP, 2 on DNS, 1 on NAT: a 3-question sample covers all three.
    items = [("tcp", i) for i in range(10)] + [("dns", i) for i in range(2)] + [("nat", 0)]
    picked = interleave_by_topic(items, topic=topic, rng=random.Random(7), limit=3)
    assert sorted(topic(i) for i in picked) == ["dns", "nat", "tcp"]


def test_without_limit_every_item_is_returned_once() -> None:
    items = [("a", i) for i in range(5)] + [("b", i) for i in range(3)]
    picked = interleave_by_topic(items, topic=topic, rng=random.Random(1))
    assert sorted(picked) == sorted(items)
    # The first rounds alternate topics until the smaller bucket runs out.
    assert Counter(topic(i) for i in picked[:6]) == {"a": 3, "b": 3}


def test_limit_larger_than_pool_and_empty_pool() -> None:
    assert len(interleave_by_topic([("a", 1)], topic=topic, rng=random.Random(), limit=5)) == 1
    assert interleave_by_topic([], topic=topic, rng=random.Random(), limit=5) == []


def test_sampling_is_reproducible_with_a_seeded_rng() -> None:
    items = [(t, i) for t in "abc" for i in range(4)]
    first = interleave_by_topic(items, topic=topic, rng=random.Random(42), limit=6)
    assert first == interleave_by_topic(items, topic=topic, rng=random.Random(42), limit=6)


def test_primary_topic() -> None:
    assert primary_topic(["dns", "reti"]) == "dns"
    assert primary_topic([]) == UNTAGGED
    assert primary_topic(None) == UNTAGGED
