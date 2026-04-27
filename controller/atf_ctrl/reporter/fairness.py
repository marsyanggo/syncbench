"""Jain's Fairness Index and related metrics."""


def jains_fairness_index(values: list[float]) -> float:
    """Compute Jain's Fairness Index for a list of throughput values.

    JFI = (Σxi)² / (n × Σxi²)

    Returns 1.0 for perfect fairness, 1/n for worst case (one STA gets all).
    """
    if not values or all(v == 0 for v in values):
        return 0.0
    n = len(values)
    return sum(values) ** 2 / (n * sum(v ** 2 for v in values))


def fairness_grade(jfi: float) -> str:
    if jfi >= 0.95:
        return "Excellent"
    if jfi >= 0.80:
        return "Good"
    if jfi >= 0.60:
        return "Fair"
    return "Poor"
