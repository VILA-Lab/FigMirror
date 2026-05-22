"""compute_set_id properties: order-independence, dedup, determinism."""
from __future__ import annotations

import pytest

from figcopy_runner.interface import compute_set_id


def test_basic_shape():
    sid = compute_set_id([1, 3, 5])
    assert isinstance(sid, str)
    assert len(sid) == 8
    # Hex chars only.
    assert all(c in "0123456789abcdef" for c in sid)


def test_order_independent():
    a = compute_set_id([5, 3, 1])
    b = compute_set_id([1, 3, 5])
    c = compute_set_id([3, 5, 1])
    assert a == b == c


def test_dedup():
    assert compute_set_id([1, 3, 3]) == compute_set_id([1, 3])
    assert compute_set_id([1, 1, 1]) == compute_set_id([1])


def test_deterministic_across_calls():
    seen = {compute_set_id([2, 4]) for _ in range(20)}
    assert len(seen) == 1


def test_different_sets_distinct():
    assert compute_set_id([1, 3, 5]) != compute_set_id([1, 3, 5, 7])
    assert compute_set_id([1]) != compute_set_id([2])


def test_single_element():
    sid = compute_set_id([7])
    assert len(sid) == 8


def test_empty_raises():
    with pytest.raises(ValueError):
        compute_set_id([])


def test_known_value():
    # Spot-check one known hash so regressions in the algorithm are
    # caught (sha1 of "1,3,5"[:8]).
    import hashlib
    expected = hashlib.sha1(b"1,3,5").hexdigest()[:8]
    assert compute_set_id([1, 3, 5]) == expected
