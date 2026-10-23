from load.metrics import cache_hit_rate


def snapshot(hits, misses):
    return {"series": {"cache_hits": hits, "cache_misses": misses}}


def test_cache_ratio_uses_deltas_per_series():
    assert cache_hit_rate(snapshot({"a": 10}, {"a": 10}), snapshot({"a": 19}, {"a": 11})) == 0.9


def test_cache_ratio_rejects_disappearing_replica_even_when_aggregate_increases():
    assert (
        cache_hit_rate(
            snapshot({"a": 10, "b": 10}, {"a": 1, "b": 1}), snapshot({"a": 100}, {"a": 10})
        )
        is None
    )


def test_cache_ratio_rejects_individual_reset_hidden_by_another_replica():
    assert (
        cache_hit_rate(
            snapshot({"a": 100, "b": 100}, {"a": 1, "b": 1}),
            snapshot({"a": 1, "b": 1000}, {"a": 2, "b": 2}),
        )
        is None
    )


def test_cache_ratio_preserves_zero_and_missing_measurements():
    assert cache_hit_rate(snapshot({"a": 10}, {"a": 10}), snapshot({"a": 10}, {"a": 20})) == 0
    assert cache_hit_rate({}, {}) is None
