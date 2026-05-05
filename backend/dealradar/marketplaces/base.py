import json
import random
import re
import time
from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from ..models import ComparableItem, ProductProfile, SourceResult
from ..text import best_title_from_context, detect_bad_words, normalize_space, simple_similarity

PRICE_EUR_RE = re.compile(
    r"(?:(?:€\s*)?(\d{1,5}(?:[.\s]\d{3})*(?:,\d{1,2})?)\s*€|€\s*(\d{1,5}(?:[,\s]\d{3})*(?:\.\d{1,2})?))",
    re.I,
)

PRICE_USD_RE = re.compile(r"\$\s*(\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)", re.I)


class MarketplaceFetcher(ABC):
    source_id = "base"
    source_name = "Base"
    source_type = "used_active"
    reliability = 0.5
    enabled = True

    def __init__(self, engine_settings: Dict, source_settings: Dict):
        self.engine_settings = engine_settings or {}
        self.source_settings = source_settings or {}
        self.enabled = bool(self.source_settings.get("enabled", True))
        self.reliability = float(self.source_settings.get("reliability", self.reliability))
        self.source_type = self.source_settings.get("type", self.source_type)
        self.timeout = float(self.engine_settings.get("request_timeout_seconds", 14))
        self.max_items = int(self.engine_settings.get("max_items_per_source", 35))
        self.delay_min = float(self.engine_settings.get("request_delay_min_seconds", 0.35))
        self.delay_max = float(self.engine_settings.get("request_delay_max_seconds", 1.25))

    @abstractmethod
    def fetch(self, profile: ProductProfile, listing: Dict, client_settings: Dict) -> SourceResult:
        ...

    def disabled(self, reason: str) -> SourceResult:
        return SourceResult(
            source_id=self.source_id,
            source_name=self.source_name,
            source_type=self.source_type,
            reliability=self.reliability,
            status="disabled",
            warnings=[reason],
        )

    def get(self, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None) -> requests.Response:
        time.sleep(random.uniform(self.delay_min, self.delay_max))
        default_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8,es;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Cache-Control": "no-cache",
        }
        if headers:
            default_headers.update(headers)

        return requests.get(url, params=params, headers=default_headers, timeout=self.timeout)

    def result_from_exception(self, exc: Exception, searched_url: str = "") -> SourceResult:
        message = str(exc)
        status = "blocked" if "403" in message or "429" in message else "error"
        return SourceResult(
            source_id=self.source_id,
            source_name=self.source_name,
            source_type=self.source_type,
            reliability=self.reliability,
            status=status,
            warnings=[f"{self.source_name} failed: {message[:180]}"],
            error=message[:220],
            searched_url=searched_url,
        )

    def generic_html_fetch(self, url: str, profile: ProductProfile, listing_price: Optional[float], allow_usd: bool = False) -> SourceResult:
        started = time.perf_counter()

        try:
            response = self.get(url)
            if response.status_code in (403, 429):
                return SourceResult(
                    source_id=self.source_id,
                    source_name=self.source_name,
                    source_type=self.source_type,
                    reliability=self.reliability,
                    status="blocked",
                    warnings=[f"{self.source_name} blocked the request with HTTP {response.status_code}."],
                    error=f"HTTP {response.status_code}",
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    searched_url=url,
                )
            response.raise_for_status()
            items = self.extract_items_from_html(response.text, url, profile, listing_price, allow_usd=allow_usd)
            return SourceResult(
                source_id=self.source_id,
                source_name=self.source_name,
                source_type=self.source_type,
                reliability=self.reliability,
                status="ok" if items else "empty",
                items=items,
                warnings=[] if items else [f"No usable prices parsed from {self.source_name}."],
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                searched_url=response.url,
            )
        except Exception as exc:
            result = self.result_from_exception(exc, url)
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return result

    def extract_items_from_html(
        self,
        html: str,
        url: str,
        profile: ProductProfile,
        listing_price: Optional[float],
        allow_usd: bool = False,
    ) -> List[ComparableItem]:
        soup = BeautifulSoup(html, "html.parser")

        # Remove irrelevant text-heavy blocks.
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        items = []
        items.extend(self._extract_jsonld_items(soup, profile, listing_price))

        text = soup.get_text(" ", strip=True)
        items.extend(self._extract_regex_items(text, url, profile, listing_price, allow_usd))

        return self.dedupe_and_filter(items, profile)

    def _extract_jsonld_items(self, soup: BeautifulSoup, profile: ProductProfile, listing_price: Optional[float]) -> List[ComparableItem]:
        items: List[ComparableItem] = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text() or ""
            if not raw.strip():
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue

            nodes = data if isinstance(data, list) else [data]
            for node in nodes:
                items.extend(self._walk_jsonld(node, profile, listing_price))
        return items

    def _walk_jsonld(self, node, profile: ProductProfile, listing_price: Optional[float]) -> List[ComparableItem]:
        found = []
        if isinstance(node, list):
            for child in node:
                found.extend(self._walk_jsonld(child, profile, listing_price))
            return found

        if not isinstance(node, dict):
            return found

        name = node.get("name") or node.get("title") or ""
        offers = node.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price = offers.get("price") if isinstance(offers, dict) else node.get("price")
        url = node.get("url") or (offers.get("url") if isinstance(offers, dict) else "") or ""

        value = parse_price_number(price)
        if name and value:
            found.append(self.make_item(str(name), value, url, profile, listing_price))

        for value in node.values():
            found.extend(self._walk_jsonld(value, profile, listing_price))

        return found

    def _extract_regex_items(
        self,
        text: str,
        url: str,
        profile: ProductProfile,
        listing_price: Optional[float],
        allow_usd: bool,
    ) -> List[ComparableItem]:
        items = []
        for match in PRICE_EUR_RE.finditer(text):
            raw = match.group(1) or match.group(2)
            price = parse_euro_price(raw)
            if not self.plausible_price(price, listing_price):
                continue

            start = max(0, match.start() - 110)
            end = min(len(text), match.end() + 40)
            context = text[start:end]
            title = best_title_from_context(context, profile.query)
            items.append(self.make_item(title, price, url, profile, listing_price))

        if allow_usd:
            for match in PRICE_USD_RE.finditer(text):
                price = parse_usd_price(match.group(1))
                if not self.plausible_price(price, listing_price):
                    continue
                start = max(0, match.start() - 110)
                end = min(len(text), match.end() + 40)
                context = text[start:end]
                title = best_title_from_context(context, profile.query)
                items.append(self.make_item(title, price, url, profile, listing_price, currency="USD"))

        return items

    def make_item(
        self,
        title: str,
        price: float,
        url: str,
        profile: ProductProfile,
        listing_price: Optional[float],
        currency: str = "EUR",
        location: str = "",
        condition: str = "unknown",
    ) -> ComparableItem:
        risk_flags = detect_bad_words(title)
        similarity = simple_similarity(profile.query, title, profile.must_have_tokens)
        return ComparableItem(
            source_id=self.source_id,
            source_name=self.source_name,
            title=normalize_space(title)[:180],
            price=round(float(price), 2),
            currency=currency,
            url=url,
            location=location,
            condition=condition,
            source_type=self.source_type,
            reliability=self.reliability,
            similarity=similarity,
            risk_flags=risk_flags,
        )

    def dedupe_and_filter(self, items: Iterable[ComparableItem], profile: ProductProfile) -> List[ComparableItem]:
        min_similarity = float(self.engine_settings.get("min_similarity", 0.34))
        seen = set()
        clean: List[ComparableItem] = []

        for item in items:
            if item.currency not in ("EUR", "USD"):
                continue

            # USD is not converted; only used as rough reference. Keep it but lower reliability.
            if item.currency == "USD":
                item.reliability *= 0.75

            # If exact model exists, strongly prefer exact matches.
            if profile.canonical_model and profile.category != "generic":
                missing = [t for t in profile.must_have_tokens if t and t not in item.title.lower()]
                if len(missing) >= 2:
                    continue

            if item.similarity < min_similarity:
                continue

            if item.risk_flags:
                # Do not let broken/for-parts comps define market price for normal listings.
                item.reliability *= 0.35

            key = (self.source_id, round(item.price, 2), item.title.lower()[:80])
            if key in seen:
                continue
            seen.add(key)
            clean.append(item)

        clean.sort(key=lambda x: (x.price, -x.similarity))
        return clean[: self.max_items]

    def plausible_price(self, price: Optional[float], listing_price: Optional[float]) -> bool:
        if price is None:
            return False

        if price < 5 or price > 20000:
            return False

        if listing_price and listing_price > 0:
            if price < max(5, listing_price * 0.08):
                return False
            if price > listing_price * 8 and listing_price > 80:
                return False

        return True


def parse_euro_price(raw: str) -> Optional[float]:
    if raw is None:
        return None
    value = str(raw).strip()
    if "," in value:
        value = value.replace(".", "").replace(" ", "").replace(",", ".")
    else:
        value = value.replace(" ", "").replace(",", "")
    return parse_price_number(value)


def parse_usd_price(raw: str) -> Optional[float]:
    if raw is None:
        return None
    return parse_price_number(str(raw).replace(",", ""))


def parse_price_number(value) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(str(value).strip())
        if number <= 0:
            return None
        return number
    except Exception:
        return None


def q(value: str) -> str:
    return quote_plus(value or "")
