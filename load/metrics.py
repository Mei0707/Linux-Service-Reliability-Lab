def cache_hit_rate(before, after):
    """Reject cache ratios when a replica appears, disappears, or resets a counter."""
    deltas = {}
    for name in ["cache_hits", "cache_misses"]:
        previous = before.get("series", {}).get(name)
        current = after.get("series", {}).get(name)
        if not previous or not current or previous.keys() != current.keys():
            return None
        differences = [current[key] - value for key, value in previous.items()]
        if any(value < 0 for value in differences):
            return None
        deltas[name] = sum(differences)
    total = deltas["cache_hits"] + deltas["cache_misses"]
    return deltas["cache_hits"] / total if total else None
