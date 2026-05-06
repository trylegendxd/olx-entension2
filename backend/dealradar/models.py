from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone


@dataclass
class ProductProfile:
    raw_title: str
    clean_title: str
    query: str
    category: str = "generic"
    brand: Optional[str] = None
    canonical_model: Optional[str] = None
    model_tokens: List[str] = field(default_factory=list)
    must_have_tokens: List[str] = field(default_factory=list)
    excluded_tokens: List[str] = field(default_factory=list)
    condition_hint: str = "unknown"
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    exact_match_required: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ComparableItem:
    source_id: str
    source_name: str
    title: str
    price: float
    currency: str = "EUR"
    url: str = ""
    location: str = ""
    condition: str = "unknown"
    source_type: str = "used_active"
    reliability: float = 0.5
    similarity: float = 0.0
    risk_flags: List[str] = field(default_factory=list)
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["price"] = round(float(self.price), 2)
        data["similarity"] = round(float(self.similarity), 3)
        return data


@dataclass
class SourceResult:
    source_id: str
    source_name: str
    source_type: str
    reliability: float
    status: str
    items: List[ComparableItem] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    elapsed_ms: int = 0
    searched_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        prices = [item.price for item in self.items if item.price is not None]
        median = None
        if prices:
            sorted_prices = sorted(prices)
            n = len(sorted_prices)
            median = sorted_prices[n // 2] if n % 2 else (sorted_prices[n // 2 - 1] + sorted_prices[n // 2]) / 2

        return {
            "id": self.source_id,
            "name": self.source_name,
            "type": self.source_type,
            "reliability": self.reliability,
            "status": self.status,
            "sampleSize": len(self.items),
            "median": round(median, 2) if median is not None else None,
            "warnings": self.warnings,
            "error": self.error,
            "elapsedMs": self.elapsed_ms,
            "searchedUrl": self.searched_url,
            "items": [item.to_dict() for item in self.items[:12]],
        }
