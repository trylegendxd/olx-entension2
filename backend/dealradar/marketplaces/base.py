import json
import random
import re
import time
from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from ..models import ComparableItem, ProductProfile, SourceResult
from ..text import (
    best_title_from_context,
    contains_token_or_phrase,
    detect_bad_words,
    exact_model_match,
    excluded_model_match,
    normalize_space,
    normalized_for_match,
    simple_similarity,
)

PRICE_EUR_RE = re.compile(
    r"(?:(?:€\s*)?(\d{1,6}(?:[.\s]\d{3})*(?:,\d{1,2})?)\s*€|€\s*(\d{1,6}(?:[,\s]\d{3})*(?:\.\d{1,2})?))",
    re.I,
)

PRICE_USD_RE = re.compile(r"\$\s*(\d{1,6}(?:,\d{3})*(?:\.\d{1,2})?)", re.I)


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
        self.timeout = float(self.engine_settings.get("request_timeout_seconds", 16))
        self.max_items = int(self.engine_settings.get("max_items_per_source", 40))
        self.delay_min = float(self.engine_settings.get("request_delay_min_seconds", 0.20))
        self.delay_max = float(self.engine_settings.get("request_delay_max_seconds", 0.85))

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
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8,es;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
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
            if response.status_code == 404:
                return SourceResult(
                    source_id=self.source_id,
                    source_name=self.source_name,
                    source_type=self.source_type,
                    reliability=self.reliability,
                    status="empty",
                    warnings=[f"{self.source_name} returned HTTP 404 for this search URL."],
                    error="HTTP 404",
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    searched_url=url,
                )

            response.raise_for_status()
            items = self.extract_items_from_html(response.text, response.url or url, profile, listing_price, allow_usd=allow_usd)
            return SourceResult(
                source_id=self.source_id,
                source_name=self.source_name,
                source_type=self.source_type,
                reliability=self.reliability,
                status="ok" if items else "empty",
                items=items,
                warnings=[] if items else [f"No trustworthy comparable prices parsed from {self.source_name}."],
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
        base_url: str,
        profile: ProductProfile,
        listing_price: Optional[float],
        allow_usd: bool = False,
    ) -> List[ComparableItem]:
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["style", "noscript", "svg"]):
            tag.decompose()

        items = []
        items.extend(self._extract_jsonld_items(soup, base_url, profile, listing_price))
        items.extend(self._extract_embedded_json_items(soup, base_url, profile, listing_price))
        items.extend(self._extract_card_items(soup, base_url, profile, listing_price))

        # Regex fallback is dangerous. Use it only when the context around the price
        # contains the exact model tokens. Never invent title=profile.query.
        text = soup.get_text(" ", strip=True)
        items.extend(self._extract_regex_items(text, base_url, profile, listing_price, allow_usd))

        return self.dedupe_and_filter(items, profile)

    def _extract_jsonld_items(self, soup: BeautifulSoup, base_url: str, profile: ProductProfile, listing_price: Optional[float]) -> List[ComparableItem]:
        items: List[ComparableItem] = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text() or ""
            if not raw.strip():
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            items.extend(self._walk_json(data, base_url, profile, listing_price))
        return items

    def _extract_embedded_json_items(self, soup: BeautifulSoup, base_url: str, profile: ProductProfile, listing_price: Optional[float]) -> List[ComparableItem]:
        items = []
        for script in soup.find_all("script"):
            raw = script.string or script.get_text() or ""
            if len(raw) < 200:
                continue

            lower = raw.lower()
            if not any(marker in lower for marker in ["price", "preço", "preco", "offers", "items", "products", "title"]):
                continue

            candidates = []
            if raw.strip().startswith("{") or raw.strip().startswith("["):
                candidates.append(raw.strip())

            m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', str(script), re.S | re.I)
            if m:
                candidates.append(m.group(1).strip())

            for candidate in candidates:
                try:
                    data = json.loads(candidate)
                    items.extend(self._walk_json(data, base_url, profile, listing_price))
                except Exception:
                    pass
        return items

    def _walk_json(self, node, base_url: str, profile: ProductProfile, listing_price: Optional[float]) -> List[ComparableItem]:
        found = []

        if isinstance(node, list):
            for child in node:
                found.extend(self._walk_json(child, base_url, profile, listing_price))
            return found

        if not isinstance(node, dict):
            return found

        title = first_nonempty(node, ["title", "name", "subject", "label", "heading"])
        url = first_nonempty(node, ["url", "href", "webUrl", "itemWebUrl", "link", "permalink"])

        price = self._extract_price_from_json_node(node)

        if title and price and self.plausible_price(price, listing_price, profile=profile):
            item = self.make_item(
                title=str(title),
                price=price,
                url=urljoin(base_url, str(url)) if url else base_url,
                profile=profile,
                listing_price=listing_price,
            )
            if self.item_matches_profile(item, profile):
                found.append(item)

        for value in node.values():
            if isinstance(value, (dict, list)):
                found.extend(self._walk_json(value, base_url, profile, listing_price))

        return found

    def _extract_price_from_json_node(self, node) -> Optional[float]:
        for key in ["price", "amount", "value", "salePrice", "currentPrice", "finalPrice"]:
            if key in node:
                parsed = parse_any_price(node[key])
                if parsed is not None:
                    return parsed

        offers = node.get("offers")
        if isinstance(offers, list):
            for offer in offers:
                parsed = self._extract_price_from_json_node(offer) if isinstance(offer, dict) else parse_any_price(offer)
                if parsed is not None:
                    return parsed
        elif isinstance(offers, dict):
            parsed = self._extract_price_from_json_node(offers)
            if parsed is not None:
                return parsed

        params = node.get("params") or node.get("parameters")
        if isinstance(params, list):
            for p in params:
                if not isinstance(p, dict):
                    continue
                key = str(p.get("key") or p.get("name") or "").lower()
                if "price" in key or "preço" in key or "preco" in key:
                    parsed = parse_any_price(p.get("value"))
                    if parsed is not None:
                        return parsed

        return None

    def _extract_card_items(self, soup: BeautifulSoup, base_url: str, profile: ProductProfile, listing_price: Optional[float]) -> List[ComparableItem]:
        items = []

        selectors = [
            "article", "li", "[data-testid]", "[data-cy]", ".product", ".item", ".card"
        ]
        candidates = []
        for selector in selectors:
            candidates.extend(soup.select(selector))

        for el in candidates[:500]:
            full_text = normalize_space(el.get_text(" ", strip=True))
            if not full_text or "€" not in full_text or len(full_text) < 8:
                continue

            # For exact products, the card itself must contain the model tokens.
            if profile.exact_match_required and not exact_model_match(full_text, profile.must_have_tokens):
                continue
            if excluded_model_match(full_text, profile.excluded_tokens):
                continue

            price = None
            m = PRICE_EUR_RE.search(full_text)
            if m:
                price = parse_euro_price(m.group(1) or m.group(2))

            if not self.plausible_price(price, listing_price, profile=profile):
                continue

            title = ""
            for title_selector in ["h1", "h2", "h3", "h4", "[title]", "a[href]", "span", "p"]:
                title_el = el.select_one(title_selector)
                if not title_el:
                    continue
                maybe = title_el.get("title") or title_el.get_text(" ", strip=True)
                maybe = normalize_space(maybe)
                if maybe and len(maybe) >= 5 and "€" not in maybe:
                    title = maybe
                    break

            if not title or (profile.exact_match_required and not exact_model_match(title + " " + full_text, profile.must_have_tokens)):
                continue

            link_el = el if getattr(el, "name", "") == "a" else el.select_one("a[href]")
            href = link_el.get("href") if link_el else ""
            item_url = urljoin(base_url, href) if href else base_url

            item = self.make_item(
                title=title,
                price=price,
                url=item_url,
                profile=profile,
                listing_price=listing_price,
            )
            if self.item_matches_profile(item, profile):
                items.append(item)

        return items

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
            if not self.plausible_price(price, listing_price, profile=profile):
                continue

            start = max(0, match.start() - 150)
            end = min(len(text), match.end() + 90)
            context = normalize_space(text[start:end])

            if profile.exact_match_required and not exact_model_match(context, profile.must_have_tokens):
                continue
            if excluded_model_match(context, profile.excluded_tokens):
                continue

            # Do not use query as fake title. Context must carry product info.
            title = best_title_from_context(context, "")
            if not title or title == profile.query:
                continue

            item = self.make_item(title, price, url, profile, listing_price)
            if self.item_matches_profile(item, profile):
                items.append(item)

        if allow_usd:
            for match in PRICE_USD_RE.finditer(text):
                price = parse_usd_price(match.group(1))
                if not self.plausible_price(price, listing_price, profile=profile):
                    continue

                start = max(0, match.start() - 150)
                end = min(len(text), match.end() + 90)
                context = normalize_space(text[start:end])

                if profile.exact_match_required and not exact_model_match(context, profile.must_have_tokens):
                    continue
                if excluded_model_match(context, profile.excluded_tokens):
                    continue

                title = best_title_from_context(context, "")
                if not title or title == profile.query:
                    continue

                item = self.make_item(title, price, url, profile, listing_price, currency="USD")
                if self.item_matches_profile(item, profile):
                    items.append(item)

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

    def item_matches_profile(self, item: ComparableItem, profile: ProductProfile) -> bool:
        title = item.title or ""

        if profile.exact_match_required:
            if not exact_model_match(title, profile.must_have_tokens):
                return False

        if excluded_model_match(title, profile.excluded_tokens):
            return False

        return True

    def dedupe_and_filter(self, items: Iterable[ComparableItem], profile: ProductProfile) -> List[ComparableItem]:
        min_similarity = float(self.engine_settings.get("min_similarity", 0.25))
        seen = set()
        clean: List[ComparableItem] = []

        for item in items:
            if item.currency not in ("EUR", "USD"):
                continue

            if not self.plausible_price(item.price, None, profile=profile):
                continue

            if item.currency == "USD":
                item.reliability *= 0.75

            if not self.item_matches_profile(item, profile):
                continue

            if item.similarity < min_similarity:
                continue

            if item.risk_flags:
                item.reliability *= 0.35

            key = (self.source_id, round(item.price, 2), item.title.lower()[:75])
            if key in seen:
                continue
            seen.add(key)
            clean.append(item)

        clean.sort(key=lambda x: (-x.similarity, x.price))
        return clean[: self.max_items]

    def plausible_price(self, price: Optional[float], listing_price: Optional[float], profile: Optional[ProductProfile] = None) -> bool:
        if price is None:
            return False

        try:
            price = float(price)
        except Exception:
            return False

        if price < 5 or price > 25000:
            return False

        if profile:
            if profile.min_price is not None and price < float(profile.min_price):
                return False
            if profile.max_price is not None and price > float(profile.max_price):
                return False

        if listing_price and listing_price > 0:
            # Keep this broad, because sometimes listing is the bargain.
            if price < max(5, listing_price * 0.15):
                return False
            if price > listing_price * 5 and listing_price > 80:
                return False

        return True


def first_nonempty(node: Dict, keys: List[str]):
    for key in keys:
        value = node.get(key)
        if value:
            return value
    return None


def parse_any_price(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for k in ["value", "amount", "label", "raw", "displayValue", "price"]:
            if k in value:
                parsed = parse_any_price(value[k])
                if parsed is not None:
                    return parsed
        return None
    if isinstance(value, list):
        for x in value:
            parsed = parse_any_price(x)
            if parsed is not None:
                return parsed
        return None

    text = str(value)
    if "€" in text or "," in text:
        m = PRICE_EUR_RE.search(text + " €" if "€" not in text else text)
        if m:
            return parse_euro_price(m.group(1) or m.group(2))

    m = re.search(r"(\d{1,6}(?:[.,]\d{1,2})?)", text.replace(" ", ""))
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except Exception:
            return None
    return None


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
