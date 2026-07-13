"""Shared score normalization for retrieval adapters."""


def lance_distance_to_relevance(distance: float) -> float:
    """Convert LanceDB distance (lower is better) to relevance (higher is better)."""
    value = max(0.0, float(distance))
    return 1.0 / (1.0 + value)
