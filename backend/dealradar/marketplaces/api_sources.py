import time
from urllib.parse import quote_plus

import requests

from .base import MarketplaceFetcher, parse_euro_price, parse_price_number
from ..models import ComparableItem, SourceResult
from ..text import exact_model_match, excluded_model_match, normalize_space, simple_similarity


class OlxApiFetcher(MarketplaceFetcher):
    source_id = "olx_api"
    source_name = "OLX API search"
    source_type = "used_active"
    reliability = 0.88

    def fetch(self, profile, listing, client_settings):
        if not self.enabled:
            return self.disabled("OLX API source disabled.")

        started = time.perf_counter()
        query = profile.query
        url = "https://www.olx.pt/api/v1/offers/"
        params = {
            "offset": 0,
            "limit": min(self.max_items, 40),
            "query": query,
        }

        try:
            response = self.get(url, params=params, headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": f"https://www.olx.pt/items/q-{quote_plus(query).replace('+', '-')}/",
                "Origin": "https://www.olx.pt",
            })

            if response.status_code in (403, 404, 429):
                return SourceResult(
                    self.source_id,
                    self.source_name,
                    self.source_type,
                    self.reliability,
                    "blocked" if response.status_code in (403, 429) else "empty",
                    warnings=[f"OLX API-style endpoint returned HTTP {response.status_code}."],
                    error=response.text[:180],
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    searched_url=response.url,
                )

            response.raise_for_status()
            data = response.json()
            rows = data.get("data") or data.get("offers") or []
            items = []

            for row in rows:
                title = row.get("title") or row.get("name") or ""
                price = self._extract_olx_price(row)
                if not title or not self.plausible_price(price, listing.get("priceValue"), profile=profile):
                    continue
                if profile.exact_match_required and not exact_model_match(title, profile.must_have_tokens):
                    continue
                if excluded_model_match(title, profile.excluded_tokens):
                    continue
                if profile.exact_match_required and not exact_model_match(title, profile.must_have_tokens):
                    continue
                if excluded_model_match(title, profile.excluded_tokens):
                    continue

                location = ""
                loc = row.get("location") or {}
                if isinstance(loc, dict):
                    location = ", ".join(str(x) for x in [
                        loc.get("city", {}).get("name") if isinstance(loc.get("city"), dict) else loc.get("city"),
                        loc.get("region", {}).get("name") if isinstance(loc.get("region"), dict) else loc.get("region"),
                    ] if x)

                item = ComparableItem(
                    source_id=self.source_id,
                    source_name=self.source_name,
                    title=normalize_space(title),
                    price=float(price),
                    currency="EUR",
                    url=row.get("url") or row.get("href") or "",
                    location=location,
                    condition="used",
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
                warnings=[] if items else ["OLX API returned no usable comparable items."],
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                searched_url=response.url,
            )
        except Exception as exc:
            result = self.result_from_exception(exc, url)
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return result

    def _extract_olx_price(self, row):
        # OLX often stores price inside params:
        # {"key":"price","value":{"value":350,"currency":"EUR","label":"350 €"}}
        params = row.get("params") or row.get("parameters") or []
        if isinstance(params, list):
            for p in params:
                key = str(p.get("key") or p.get("name") or p.get("code") or "").lower()
                if "price" not in key and "preço" not in key and "preco" not in key:
                    continue
                value = p.get("value")
                parsed = self._price_from_any(value)
                if parsed is not None:
                    return parsed

        for key in ["price", "salary", "value"]:
            if key in row:
                parsed = self._price_from_any(row.get(key))
                if parsed is not None:
                    return parsed

        return None

    def _price_from_any(self, value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, dict):
            for k in ["value", "amount", "label", "displayValue"]:
                if k in value:
                    parsed = self._price_from_any(value[k])
                    if parsed is not None:
                        return parsed
        text = str(value)
        if "€" in text:
            import re
            m = re.search(r"(\d{1,5}(?:[.\s]\d{3})*(?:,\d{1,2})?)", text)
            if m:
                return parse_euro_price(m.group(1))
        return parse_price_number(text.replace("EUR", "").strip())


class WallapopApiFetcher(MarketplaceFetcher):
    source_id = "wallapop_api"
    source_name = "Wallapop API search"
    source_type = "used_active"
    reliability = 0.78

    def fetch(self, profile, listing, client_settings):
        if not self.enabled:
            return self.disabled("Wallapop API source disabled.")

        started = time.perf_counter()
        url = "https://api.wallapop.com/api/v3/general/search"
        params = {
            "keywords": profile.query,
            "latitude": "41.5454",
            "longitude": "-8.4265",
            "filters_source": "search_box",
            "order_by": "closest",
        }

        try:
            response = self.get(url, params=params, headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://pt.wallapop.com",
                "Referer": "https://pt.wallapop.com/",
                "X-AppVersion": "82740",
                "X-DeviceOS": "0",
            })

            if response.status_code in (403, 429):
                return SourceResult(
                    self.source_id,
                    self.source_name,
                    self.source_type,
                    self.reliability,
                    "blocked",
                    warnings=[f"Wallapop API returned HTTP {response.status_code}."],
                    error=response.text[:180],
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    searched_url=response.url,
                )

            response.raise_for_status()
            data = response.json()
            rows = data.get("search_objects") or data.get("items") or data.get("data") or []
            items = []

            for row in rows:
                if isinstance(row, dict) and "content" in row and isinstance(row["content"], dict):
                    row = row["content"]

                title = row.get("title") or row.get("name") or ""
                price = row.get("price") or row.get("sale_price") or row.get("amount")
                if isinstance(price, dict):
                    price = price.get("amount") or price.get("value")

                try:
                    price = float(price)
                except Exception:
                    continue

                if not title or not self.plausible_price(price, listing.get("priceValue"), profile=profile):
                    continue
                if profile.exact_match_required and not exact_model_match(title, profile.must_have_tokens):
                    continue
                if excluded_model_match(title, profile.excluded_tokens):
                    continue
                if profile.exact_match_required and not exact_model_match(title, profile.must_have_tokens):
                    continue
                if excluded_model_match(title, profile.excluded_tokens):
                    continue

                web_slug = row.get("web_slug") or row.get("slug") or ""
                item_url = row.get("url") or (f"https://pt.wallapop.com/item/{web_slug}" if web_slug else "")

                item = ComparableItem(
                    source_id=self.source_id,
                    source_name=self.source_name,
                    title=normalize_space(title),
                    price=price,
                    currency="EUR",
                    url=item_url,
                    location=str(row.get("location") or ""),
                    condition=str(row.get("condition") or "used").lower(),
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
                warnings=[] if items else ["Wallapop API returned no usable comparable items."],
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                searched_url=response.url,
            )
        except Exception as exc:
            result = self.result_from_exception(exc, url)
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return result
