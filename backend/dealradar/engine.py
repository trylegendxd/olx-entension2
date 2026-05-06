import concurrent.futures
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .cache import TTLCache
from .models import ComparableItem, SourceResult
from .query import QueryBuilder
from .stats import coefficient_of_variation, trim_iqr, weighted_median
from .text import detect_bad_words, exact_model_match, excluded_model_match, simple_similarity
from .marketplaces.api_sources import OlxApiFetcher, WallapopApiFetcher
from .marketplaces.search_fallback import DuckDuckGoSearchFetcher
from .marketplaces.html_sources import (
    CustoJustoFetcher,
    KuantoKustaFetcher,
    OlxFetcher,
    WallapopFetcher,
    WortenFetcher,
)
from .marketplaces.ebay import EbayBrowseFetcher, EbayHtmlFetcher, SerpApiEbaySoldFetcher


class DealRadarEngine:
    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.engine_settings = self.config.get("engine", {})
        self.source_settings = self.config.get("sources", {})
        self.query_builder = QueryBuilder()
        self.cache = TTLCache(
            ttl_seconds=int(self.engine_settings.get("cache_ttl_seconds", 900)),
            max_size=512,
        )
        self.fetcher_classes = [
            OlxApiFetcher,
            WallapopApiFetcher,
            OlxFetcher,
            CustoJustoFetcher,
            WallapopFetcher,
            KuantoKustaFetcher,
            WortenFetcher,
            EbayBrowseFetcher,
            SerpApiEbaySoldFetcher,
            EbayHtmlFetcher,
            DuckDuckGoSearchFetcher,
        ]

    def describe_sources(self) -> Dict[str, Any]:
        return {
            "sources": [
                {
                    "id": cls.source_id,
                    "name": cls.source_name,
                    "type": cls.source_type,
                    "configured": self._is_configured(cls.source_id),
                    "enabled": self.source_settings.get(cls.source_id, {}).get("enabled", True),
                    "reliability": self.source_settings.get(cls.source_id, {}).get("reliability", cls.reliability),
                }
                for cls in self.fetcher_classes
            ]
        }

    def debug_query(self, title: str) -> Dict[str, Any]:
        profile = self.query_builder.build(title, "")
        return {"profile": profile.to_dict()}

    def evaluate(self, listing: Dict[str, Any], client_settings: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()

        title = str(listing.get("title") or listing.get("rawTitle") or "").strip()
        description = str(listing.get("description") or "")
        price = self._to_float(listing.get("priceValue"))

        if not title:
            return self._fatal("Missing listing title.", listing)

        if price is None:
            return self._fatal("Missing or invalid listing price.", listing)

        listing["priceValue"] = price

        profile = self.query_builder.build(title, description)
        risk_flags = sorted(set((listing.get("riskyWords") or []) + detect_bad_words(f"{title}\n{description}")))

        cache_key = f"v4|{profile.query}|{round(price, 2)}|{client_settings.get('mode', 'resale')}"
        cached = self.cache.get(cache_key)
        if cached:
            cached["cacheHit"] = True
            cached["evaluatedInMs"] = int((time.perf_counter() - started) * 1000)
            cached["listing"] = listing
            return cached

        if not self.engine_settings.get("enable_web_sources", True):
            source_results = []
        else:
            source_results = self._fetch_all(profile, listing, client_settings)

        comparable_items = self._collect_comparables(source_results, profile, price)
        market = self._calculate_market(comparable_items)

        result = self._score(
            listing=listing,
            profile=profile,
            price=price,
            risk_flags=risk_flags,
            source_results=source_results,
            market=market,
            client_settings=client_settings,
        )

        result["cacheHit"] = False
        result["evaluatedInMs"] = int((time.perf_counter() - started) * 1000)

        self.cache.set(cache_key, result)
        return result

    def _fetch_all(self, profile, listing, client_settings) -> List[SourceResult]:
        fetchers = []
        for cls in self.fetcher_classes:
            settings = self.source_settings.get(cls.source_id, {})
            fetchers.append(cls(self.engine_settings, settings))

        max_workers = int(self.engine_settings.get("max_workers", 7))
        max_workers = max(1, min(max_workers, len(fetchers)))

        results: List[SourceResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(fetcher.fetch, profile, listing, client_settings): fetcher
                for fetcher in fetchers
            }

            for future in concurrent.futures.as_completed(future_map):
                fetcher = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = SourceResult(
                        source_id=fetcher.source_id,
                        source_name=fetcher.source_name,
                        source_type=fetcher.source_type,
                        reliability=fetcher.reliability,
                        status="error",
                        warnings=[f"{fetcher.source_name} crashed: {str(exc)[:180]}"],
                        error=str(exc)[:220],
                    )
                results.append(result)

        source_order = {cls.source_id: i for i, cls in enumerate(self.fetcher_classes)}
        results.sort(key=lambda r: source_order.get(r.source_id, 999))
        return results

    def _collect_comparables(self, source_results: List[SourceResult], profile, listing_price: float) -> List[ComparableItem]:
        items: List[ComparableItem] = []

        for result in source_results:
            if result.status not in ("ok", "empty"):
                continue

            for item in result.items:
                if item.price is None or item.price <= 0:
                    continue

                if profile.min_price is not None and item.price < float(profile.min_price):
                    continue
                if profile.max_price is not None and item.price > float(profile.max_price):
                    continue

                if profile.exact_match_required:
                    if not exact_model_match(item.title, profile.must_have_tokens):
                        continue
                    if excluded_model_match(item.title, profile.excluded_tokens):
                        continue

                item.similarity = max(item.similarity, simple_similarity(profile.query, item.title, profile.must_have_tokens))

                if item.source_type == "retail_reference":
                    item.reliability *= 0.55
                if item.source_type == "search_snippet":
                    item.reliability *= 0.35

                if item.currency == "USD":
                    item.price = round(item.price * 0.92, 2)
                    item.currency = "EUR"
                    item.reliability *= 0.80

                if listing_price > 100 and item.price < listing_price * 0.15:
                    continue
                if listing_price > 80 and item.price > listing_price * 5:
                    continue

                if item.similarity < float(self.engine_settings.get("min_similarity", 0.25)):
                    continue

                items.append(item)

        seen = set()
        deduped = []
        for item in items:
            key = (round(item.price, -1) if item.price >= 100 else round(item.price, 0), item.title.lower()[:55])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        return deduped

    def _calculate_market(self, items: List[ComparableItem]) -> Dict[str, Any]:
        min_items = int(self.engine_settings.get("min_comparable_items", 3))

        if not items:
            return {
                "median": None,
                "usedMedian": None,
                "retailMedian": None,
                "sampleSize": 0,
                "usedSampleSize": 0,
                "retailSampleSize": 0,
                "confidence": "low",
                "spread": None,
                "trusted": False,
                "reason": "No comparable items passed filtering.",
            }

        used = [item for item in items if item.source_type in ("used_active", "sold", "search_snippet")]
        retail = [item for item in items if item.source_type == "retail_reference"]

        used_prices = trim_iqr([item.price for item in used])
        retail_prices = trim_iqr([item.price for item in retail])

        used_count = len(used_prices)
        retail_count = len(retail_prices)
        sample_size = used_count + retail_count

        # Critical fix: do not publish a median/profit from 1-2 scraped prices.
        if used_count < min_items:
            retail_median = weighted_median([
                (item.price, item.reliability * max(0.40, item.similarity))
                for item in retail
                if item.price in retail_prices
            ]) if retail_count >= min_items else None

            return {
                "median": None,
                "usedMedian": None,
                "retailMedian": round(retail_median, 2) if retail_median is not None else None,
                "sampleSize": sample_size,
                "usedSampleSize": used_count,
                "retailSampleSize": retail_count,
                "confidence": "low",
                "spread": None,
                "trusted": False,
                "reason": f"Only {used_count} used-market comparable(s). Need at least {min_items}.",
            }

        weighted_pairs: List[Tuple[float, float]] = []
        for item in used:
            if item.price in used_prices:
                source_type_weight = 1.35 if item.source_type == "sold" else 1.0
                if item.source_type == "search_snippet":
                    source_type_weight = 0.25
                weight = item.reliability * source_type_weight * max(0.40, item.similarity)
                weighted_pairs.append((item.price, weight))

        # Retail is only a reference, never the main used-market valuation.
        retail_median = weighted_median([
            (item.price, item.reliability * max(0.40, item.similarity))
            for item in retail
            if item.price in retail_prices
        ]) if retail_count >= min_items else None

        used_median = weighted_median(weighted_pairs)
        median = used_median

        spread = coefficient_of_variation(used_prices)

        if used_count >= 10 and spread is not None and spread < 0.28:
            confidence = "high"
        elif used_count >= 5:
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "median": round(median, 2) if median is not None else None,
            "usedMedian": round(used_median, 2) if used_median is not None else None,
            "retailMedian": round(retail_median, 2) if retail_median is not None else None,
            "sampleSize": sample_size,
            "usedSampleSize": used_count,
            "retailSampleSize": retail_count,
            "confidence": confidence,
            "spread": round(spread, 3) if spread is not None else None,
            "trusted": True,
            "reason": None,
        }

    def _score(self, listing, profile, price, risk_flags, source_results, market, client_settings) -> Dict[str, Any]:
        min_profit_pct = self._to_float(client_settings.get("minProfitPct")) or float(self.engine_settings.get("default_min_profit_pct", 25))
        min_profit_eur = self._to_float(client_settings.get("minimumProfitEuro")) or float(self.engine_settings.get("default_min_profit_eur", 30))
        fee_pct = self._to_float(client_settings.get("feePct")) or float(self.engine_settings.get("default_fee_pct", 8))
        tax_pct = self._to_float(client_settings.get("taxPct")) or float(self.engine_settings.get("default_tax_pct", 0))
        mode = client_settings.get("mode", "resale")

        market_price = market.get("usedMedian")
        sample_size = int(market.get("sampleSize") or 0)
        used_sample_size = int(market.get("usedSampleSize") or 0)

        warnings = []
        if risk_flags:
            warnings.append(f"Risk words found in listing: {', '.join(risk_flags)}.")

        if not market.get("trusted"):
            warnings.append(market.get("reason") or "Not enough trusted comparable items.")

        failed = [r for r in source_results if r.status in ("blocked", "error")]
        if failed:
            warnings.append(f"{len(failed)} source(s) failed or were blocked; verdict uses the remaining sources.")

        if profile.condition_hint == "for_parts":
            warnings.append("Listing appears to be damaged/for-parts. Resale value is much harder to estimate.")
        elif profile.condition_hint == "mining_risk":
            warnings.append("Listing mentions mining/mineração. Inspect temps, warranty, artifacts and stress-test before buying.")

        estimated_profit = None
        profit_pct = None
        below_market_pct = None

        if market_price is None or not market.get("trusted"):
            verdict = "UNKNOWN"
            summary = "Not enough reliable comparable prices. I am not calculating profit from 1-2 random scraped prices."
        else:
            estimated_sale_after_costs = market_price * (1 - (fee_pct + tax_pct) / 100)
            estimated_profit = round(estimated_sale_after_costs - price, 2)
            profit_pct = round((estimated_profit / price) * 100, 1) if price > 0 else None
            below_market_pct = round((1 - price / market_price) * 100, 1) if market_price > 0 else None

            if "avariado" in risk_flags or "danificado" in risk_flags or "não funciona" in risk_flags or "nao funciona" in risk_flags or "peças" in risk_flags or "pecas" in risk_flags:
                verdict = "AVOID"
            elif estimated_profit >= min_profit_eur and profit_pct is not None and profit_pct >= min_profit_pct and below_market_pct is not None and below_market_pct >= 18:
                verdict = "RESALE_BUY"
            elif below_market_pct is not None and below_market_pct >= 12:
                verdict = "GOOD_DEAL"
            elif below_market_pct is not None and below_market_pct >= -8:
                verdict = "FAIR"
            else:
                verdict = "AVOID"

            if mode == "personal" and verdict == "RESALE_BUY":
                verdict = "GOOD_DEAL"

            summary = self._summary(verdict, below_market_pct, estimated_profit, used_sample_size, market)

        manual_links = self._manual_links(profile.query)

        return {
            "verdict": verdict,
            "confidence": market.get("confidence", "low"),
            "summary": summary,
            "queryUsed": profile.query,
            "productProfile": profile.to_dict(),
            "marketMedian": market.get("median"),
            "usedMarketMedian": market.get("usedMedian"),
            "retailMedian": market.get("retailMedian"),
            "sampleSize": sample_size,
            "usedSampleSize": market.get("usedSampleSize", 0),
            "retailSampleSize": market.get("retailSampleSize", 0),
            "estimatedSalePrice": market_price,
            "estimatedProfit": estimated_profit,
            "profitPct": profit_pct,
            "belowMarketPct": below_market_pct,
            "riskFlags": risk_flags,
            "warnings": warnings,
            "sources": [r.to_dict() for r in source_results],
            "manualLinks": manual_links,
            "listing": listing,
        }

    def _summary(self, verdict, below_market_pct, estimated_profit, used_sample_size, market) -> str:
        if verdict == "RESALE_BUY":
            return f"Strong resale candidate: about {below_market_pct:.1f}% below used-market median, estimated profit around €{estimated_profit:.0f} from {used_sample_size} used comps."
        if verdict == "GOOD_DEAL":
            return f"Good deal: about {below_market_pct:.1f}% below estimated used-market value, but resale margin may not be huge after fees."
        if verdict == "FAIR":
            return f"Fair price: close to the estimated used-market value from {used_sample_size} comparable results."
        if verdict == "AVOID":
            return "Avoid or inspect very carefully: risk/price does not justify buying for resale."
        return "Unknown: not enough reliable comparable data."

    def _manual_links(self, query: str) -> List[Dict[str, str]]:
        from urllib.parse import quote_plus
        encoded = quote_plus(query)
        slug = encoded.replace("+", "-")
        return [
            {"name": "OLX", "url": f"https://www.olx.pt/items/q-{slug}/"},
            {"name": "CustoJusto", "url": f"https://www.custojusto.pt/portugal?q={encoded}"},
            {"name": "Wallapop", "url": f"https://pt.wallapop.com/search?keywords={encoded}"},
            {"name": "KuantoKusta", "url": f"https://www.kuantokusta.pt/search?q={encoded}"},
            {"name": "Worten", "url": f"https://www.worten.pt/search?query={encoded}"},
            {"name": "eBay active", "url": f"https://www.ebay.com/sch/i.html?_nkw={encoded}&LH_BIN=1"},
            {"name": "eBay sold", "url": f"https://www.ebay.com/sch/i.html?_nkw={encoded}&LH_Sold=1&LH_Complete=1"},
        ]

    def _fatal(self, reason: str, listing: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "fatalError": True,
            "verdict": "UNKNOWN",
            "confidence": "low",
            "summary": reason,
            "warnings": [reason],
            "sources": [],
            "listing": listing,
        }

    def _to_float(self, value) -> Optional[float]:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    def _is_configured(self, source_id: str) -> bool:
        if source_id == "ebay_browse":
            return bool(os.getenv("EBAY_BEARER_TOKEN") or (os.getenv("EBAY_CLIENT_ID") and os.getenv("EBAY_CLIENT_SECRET")))
        if source_id == "serpapi_ebay_sold":
            return bool(os.getenv("SERPAPI_KEY"))
        return True
