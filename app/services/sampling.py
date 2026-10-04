"""Topic-balanced sampling for practice simulations."""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")

UNTAGGED = "__untagged__"


def primary_topic(tags: Sequence[str] | None) -> str:
    """Bucket key for a question: its first tag (tags are sorted by slug), if any."""
    return tags[0] if tags else UNTAGGED


def interleave_by_topic(
    items: Sequence[T],
    *,
    topic: Callable[[T], str],
    rng: random.Random,
    limit: int | None = None,
) -> list[T]:
    """Shuffle within topics, then pick round-robin across topics.

    Plain random sampling over-represents the topics with the most past questions;
    round-robin keeps a short simulation spread over as many topics as possible.
    Returns at most `limit` items (all of them when `limit` is None).
    """
    buckets: dict[str, list[T]] = defaultdict(list)
    for item in items:
        buckets[topic(item)].append(item)
    for bucket in buckets.values():
        rng.shuffle(bucket)
    order = list(buckets)
    rng.shuffle(order)

    target = len(items) if limit is None else min(limit, len(items))
    picked: list[T] = []
    round_idx = 0
    while len(picked) < target:
        for key in order:
            bucket = buckets[key]
            if round_idx < len(bucket):
                picked.append(bucket[round_idx])
                if len(picked) == target:
                    break
        round_idx += 1
    return picked
