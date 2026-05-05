import concurrent.futures
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .cache import TTLCache
from .models import ComparableItem, SourceResult
from .query import QueryBuilder
from .stats import coefficient_of_variation, trim_iqr, weighted_median
from .text import detect_bad_words, simple_similarity
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
            OlxFetcher,
            CustoJustoFetcher,
            WallapopFetcher,
            KuantoKustaFetcher,
            WortenFetcher,
            EbayBrowseFetcher,
            SerpApiEbaySoldFetcher,
            EbayHtmlFetcher,
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

        cache_key = f"{profile.query}|{round(price, 2)}|{client_settings.get('mode', 'resale')}"
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

        # Do not cache fatal/empty noise too aggressively? Still cache briefly through TTL.
        self.cache.set(cache_key, result)
        return result

    def _fetch_all(self, profile, listing, client_settings) -> List[SourceResult]:
        fetchers = []
        for cls in self.fetcher_classes:
            settings = self.source_settings.get(cls.source_id, {})
            fetchers.append(cls(self.engine_settings, settings))

        max_workers = int(self.engine_settings.get("max_workers", 5))
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

                # Normalize final similarity against the cleaned listing query.
                item.similarity = max(item.similarity, simple_similarity(profile.query, item.title, profile.must_have_tokens))

                # Strongly downweight retail when estimating used resale.
                if item.source_type == "retail_reference":
                    item.reliability *= 0.55

                if item.currency == "USD":
                    # Rough EUR approximation. For production, replace with cached FX API.
                    item.price = round(item.price * 0.92, 2)
                    item.currency = "EUR"
                    item.reliability *= 0.80

                # Exclude obvious accessories/too-cheap comps.
                if listing_price > 100 and item.price < listing_price * 0.15:
                    continue

                if item.similarity < float(self.engine_settings.get("min_similarity", 0.34)):
                    continue

                items.append(item)

        # Dedupe across sources.
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
            }

        used = [item for item in items if item.source_type in ("used_active", "sold")]
        retail = [item for item in items if item.source_type == "retail_reference"]

        used_prices = trim_iqr([item.price for item in used])
        retail_prices = trim_iqr([item.price for item in retail])

        weighted_pairs: List[Tuple[float, float]] = []
        for item in items:
            if item.price in used_prices or item.price in retail_prices:
                source_type_weight = 1.25 if item.source_type == "sold" else 1.0
                if item.source_type == "retail_reference":
                    source_type_weight = 0.45
                weight = item.reliability * source_type_weight * max(0.40, item.similarity)
                weighted_pairs.append((item.price, weight))

        median = weighted_median(weighted_pairs)
        used_median = weighted_median([
            (item.price, item.reliability * max(0.40, item.similarity))
            for item in used
            if item.price in used_prices
        ])
        retail_median = weighted_median([
            (item.price, item.reliability * max(0.40, item.similarity))
            for item in retail
            if item.price in retail_prices
        ])

        used_count = len(used_prices)
        retail_count = len(retail_prices)
        sample_size = used_count + retail_count
        spread = coefficient_of_variation(used_prices or [p for p, _ in weighted_pairs])

        if used_count >= 10 and spread is not None and spread < 0.28:
            confidence = "high"
        elif used_count >= 5 or sample_size >= 8:
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
        }

    def _score(self, listing, profile, price, risk_flags, source_results, market, client_settings) -> Dict[str, Any]:
        min_profit_pct = self._to_float(client_settings.get("minProfitPct")) or float(self.engine_settings.get("default_min_profit_pct", 25))
        min_profit_eur = self._to_float(client_settings.get("minimumProfitEuro")) or float(self.engine_settings.get("default_min_profit_eur", 30))
        fee_pct = self._to_float(client_settings.get("feePct")) or float(self.engine_settings.get("default_fee_pct", 8))
        tax_pct = self._to_float(client_settings.get("taxPct")) or float(self.engine_settings.get("default_tax_pct", 0))
        mode = client_settings.get("mode", "resale")

        market_price = market.get("usedMedian") or market.get("median")
        sample_size = int(market.get("sampleSize") or 0)
        used_sample_size = int(market.get("usedSampleSize") or 0)

        warnings = []
        if risk_flags:
            warnings.append(f"Risk words found in listing: {', '.join(risk_flags)}.")

        if sample_size < int(self.engine_settings.get("min_comparable_items", 4)):
            warnings.append("Very small sample size. Treat result as low-confidence and manually verify.")

        failed = [r for r in source_results if r.status in ("blocked", "error")]
        if failed:
            warnings.append(f"{len(failed)} source(s) failed or were blocked; verdict uses the remaining sources.")

        if profile.condition_hint == "for_parts":
            warnings.append("Listing appears to be damaged/for-parts. Resale value is much harder to estimate.")

        if market_price is None:
            verdict = "UNKNOWN"
            summary = "No reliable comparable market price found. Manual verification required."
            estimated_profit = None
            profit_pct = None
            below_market_pct = None
        else:
            estimated_sale_after_costs = market_price * (1 - (fee_pct + tax_pct) / 100)
            estimated_profit = round(estimated_sale_after_costs - price, 2)
            profit_pct = round((estimated_profit / price) * 100, 1) if price > 0 else None
            below_market_pct = round((1 - price / market_price) * 100, 1) if market_price > 0 else None

            if risk_flags and profile.condition_hint != "new_or_like_new":
                verdict = "AVOID"
            elif used_sample_size < 3 and market.get("retailMedian"):
                if price <= market["retailMedian"] * 0.55:
                    verdict = "GOOD_DEAL"
                else:
                    verdict = "UNKNOWN"
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

            summary = self._summary(verdict, below_market_pct, estimated_profit, sample_size, market)

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

    def _summary(self, verdict, below_market_pct, estimated_profit, sample_size, market) -> str:
        if verdict == "RESALE_BUY":
            return f"Strong resale candidate: about {below_market_pct:.1f}% below used-market median, estimated profit around €{estimated_profit:.0f} from {sample_size} comps."
        if verdict == "GOOD_DEAL":
            if estimated_profit is not None:
                return f"Good deal: about {below_market_pct:.1f}% below estimated market value, but resale margin may not be huge after fees."
            return "Good personal deal versus retail reference, but used-market data is weak."
        if verdict == "FAIR":
            return f"Fair price: close to the estimated market value from {sample_size} comparable results."
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
