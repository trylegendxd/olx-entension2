import math
import statistics
from typing import Iterable, List, Optional, Tuple


def percentile(values: List[float], pct: float) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    k = (len(values) - 1) * (pct / 100)
    f = math.floor(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[int(k)]
    return values[f] + (values[c] - values[f]) * (k - f)


def trim_iqr(values: Iterable[float]) -> List[float]:
    values = sorted(float(v) for v in values if v is not None and float(v) > 0)
    if len(values) < 5:
        return values

    q1 = percentile(values, 25)
    q3 = percentile(values, 75)
    iqr = q3 - q1
    if iqr <= 0:
        return values

    low = max(0.0, q1 - 1.5 * iqr)
    high = q3 + 1.5 * iqr
    return [v for v in values if low <= v <= high]


def weighted_median(pairs: List[Tuple[float, float]]) -> Optional[float]:
    clean = sorted((float(price), max(0.01, float(weight))) for price, weight in pairs if price is not None and price > 0)
    if not clean:
        return None

    total_weight = sum(weight for _, weight in clean)
    midpoint = total_weight / 2
    cumulative = 0.0

    for price, weight in clean:
        cumulative += weight
        if cumulative >= midpoint:
            return price

    return clean[-1][0]


def coefficient_of_variation(values: List[float]) -> Optional[float]:
    values = [float(v) for v in values if v is not None and v > 0]
    if len(values) < 2:
        return None

    mean = statistics.mean(values)
    if mean <= 0:
        return None

    return statistics.pstdev(values) / mean
