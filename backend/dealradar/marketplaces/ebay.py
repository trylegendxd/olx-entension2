import base64
import os
import time
from typing import Dict, Optional

import requests

from .base import MarketplaceFetcher, q
from ..models import ComparableItem, SourceResult
from ..text import exact_model_match, excluded_model_match, normalize_space, simple_similarity


class EbayBrowseFetcher(MarketplaceFetcher):
    source_id = "ebay_browse"
    source_name = "eBay Browse API"
    source_type = "used_active"
    reliability = 0.76

    token_cache: Dict[str, object] = {"token": None, "expires_at": 0}

    def fetch(self, profile, listing, client_settings):
        if not self.enabled:
            return self.disabled("eBay Browse source disabled.")

        client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
        client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()
        bearer = os.getenv("EBAY_BEARER_TOKEN", "").strip()

        if not bearer and not (client_id and client_secret):
            return self.disabled("Missing EBAY_BEARER_TOKEN or EBAY_CLIENT_ID/EBAY_CLIENT_SECRET.")

        started = time.perf_counter()
        url = "https://api.ebay.com/buy/browse/v1/item_summary/search"

        try:
            token = bearer or self.get_oauth_token(client_id, client_secret)
            headers = {
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": os.getenv("EBAY_MARKETPLACE_ID", "EBAY_ES"),
                "Accept": "application/json",
            }
            params = {
                "q": profile.query,
                "limit": min(self.max_items, 50),
                "filter": "buyingOptions:{FIXED_PRICE}",
            }

            response = requests.get(url, headers=headers, params=params, timeout=self.timeout)
            if response.status_code in (401, 403, 429):
                return SourceResult(
                    self.source_id,
                    self.source_name,
                    self.source_type,
                    self.reliability,
                    "blocked",
                    warnings=[f"eBay Browse API returned HTTP {response.status_code}."],
                    error=response.text[:200],
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    searched_url=response.url,
                )

            response.raise_for_status()
            data = response.json()
            items = []
            for row in data.get("itemSummaries", []):
                title = row.get("title") or ""
                price_data = row.get("price") or row.get("currentBidPrice") or {}
                value = price_data.get("value")
                currency = price_data.get("currency", "EUR")
                try:
                    price = float(value)
                except Exception:
                    continue

                if not self.plausible_price(price, listing.get("priceValue"), profile=profile):
                    continue
                if profile.exact_match_required and not exact_model_match(title, profile.must_have_tokens):
                    continue
                if excluded_model_match(title, profile.excluded_tokens):
                    continue

                item = ComparableItem(
                    source_id=self.source_id,
                    source_name=self.source_name,
                    title=normalize_space(title),
                    price=price,
                    currency=currency,
                    url=row.get("itemWebUrl", ""),
                    location=", ".join(x for x in [
                        (row.get("itemLocation") or {}).get("city", ""),
                        (row.get("itemLocation") or {}).get("country", "")
                    ] if x),
                    condition=(row.get("condition") or "unknown").lower(),
                    source_type=self.source_type,
                    reliability=self.reliability,
                    similarity=simple_similarity(profile.query, title, profile.must_have_tokens),
                    risk_flags=[],
                )
                items.append(item)

            items = self.dedupe_and_filter(items, profile)
            return SourceResult(
                self.source_id,
                self.source_name,
                self.source_type,
                self.reliability,
                "ok" if items else "empty",
                items=items,
                warnings=[] if items else ["eBay API returned no comparable items."],
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                searched_url=response.url,
            )
        except Exception as exc:
            result = self.result_from_exception(exc, url)
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return result

    def get_oauth_token(self, client_id: str, client_secret: str) -> str:
        now = time.time()
        if self.token_cache["token"] and now < float(self.token_cache["expires_at"]):
            return str(self.token_cache["token"])

        credentials = f"{client_id}:{client_secret}".encode("utf-8")
        basic = base64.b64encode(credentials).decode("ascii")

        response = requests.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        token = data["access_token"]
        expires_in = int(data.get("expires_in", 7200))

        self.token_cache["token"] = token
        self.token_cache["expires_at"] = now + max(60, expires_in - 120)
        return token


class SerpApiEbaySoldFetcher(MarketplaceFetcher):
    source_id = "serpapi_ebay_sold"
    source_name = "SerpApi eBay Sold"
    source_type = "sold"
    reliability = 0.95

    def fetch(self, profile, listing, client_settings):
        if not self.enabled:
            return self.disabled("SerpApi eBay source disabled.")

        api_key = os.getenv("SERPAPI_KEY", "").strip()
        if not api_key:
            return self.disabled("Missing SERPAPI_KEY.")

        started = time.perf_counter()
        url = "https://serpapi.com/search.json"

        try:
            params = {
                "engine": "ebay",
                "_nkw": profile.query,
                "api_key": api_key,
                "show_only": "Sold",
                "ebay_domain": "ebay.com",
            }
            response = requests.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            rows = data.get("organic_results") or data.get("results") or []
            items = []

            for row in rows:
                title = row.get("title") or row.get("name") or ""
                price = self._extract_price(row)
                if price is None or not self.plausible_price(price, listing.get("priceValue"), profile=profile):
                    continue
                if profile.exact_match_required and not exact_model_match(title, profile.must_have_tokens):
                    continue
                if excluded_model_match(title, profile.excluded_tokens):
                    continue

                item = self.make_item(
                    title=title,
                    price=price,
                    url=row.get("link") or row.get("url") or "",
                    profile=profile,
                    listing_price=listing.get("priceValue"),
                    currency="EUR",
                    condition=str(row.get("condition") or "sold").lower(),
                )
                item.source_type = "sold"
                item.reliability = self.reliability
                items.append(item)

            items = self.dedupe_and_filter(items, profile)

            return SourceResult(
                self.source_id,
                self.source_name,
                self.source_type,
                self.reliability,
                "ok" if items else "empty",
                items=items,
                warnings=[] if items else ["SerpApi returned no sold comparable items."],
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                searched_url=response.url.replace(api_key, "***"),
            )
        except Exception as exc:
            result = self.result_from_exception(exc, url)
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return result

    def _extract_price(self, row) -> Optional[float]:
        candidates = [
            row.get("price"),
            row.get("extracted_price"),
            row.get("primary_price"),
            row.get("buy_it_now_price"),
        ]
        for value in candidates:
            if value is None:
                continue
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, dict):
                for key in ["extracted", "value", "raw"]:
                    if key in value:
                        maybe = self._extract_price({ "price": value[key] })
                        if maybe is not None:
                            return maybe
            text = str(value)
            import re
            match = re.search(r"(\d{1,5}(?:[.,]\d{1,2})?)", text.replace(",", "."))
            if match:
                try:
                    return float(match.group(1))
                except Exception:
                    pass
        return None


class EbayHtmlFetcher(MarketplaceFetcher):
    source_id = "ebay_html"
    source_name = "eBay HTML fallback"
    source_type = "used_active"
    reliability = 0.50

    def fetch(self, profile, listing, client_settings):
        if not self.enabled:
            return self.disabled("eBay HTML fallback disabled.")
        url = f"https://www.ebay.com/sch/i.html?_nkw={q(profile.query)}&LH_BIN=1"
        return self.generic_html_fetch(url, profile, listing.get("priceValue"), allow_usd=True)
