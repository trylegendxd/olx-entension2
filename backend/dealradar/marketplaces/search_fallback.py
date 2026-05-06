import re
import time
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

from bs4 import BeautifulSoup

from .base import MarketplaceFetcher, PRICE_EUR_RE, parse_euro_price
from ..models import SourceResult
from ..text import exact_model_match, excluded_model_match


class DuckDuckGoSearchFetcher(MarketplaceFetcher):
    source_id = "duckduckgo_search"
    source_name = "DuckDuckGo marketplace fallback"
    source_type = "search_snippet"
    reliability = 0.38

    def fetch(self, profile, listing, client_settings):
        if not self.enabled:
            return self.disabled("DuckDuckGo fallback disabled.")

        started = time.perf_counter()

        # The query asks for price snippets from multiple marketplaces.
        query = (
            f'"{profile.query}" preço € '
            f'(site:olx.pt OR site:custojusto.pt OR site:wallapop.com OR site:kuantokusta.pt OR site:worten.pt)'
        )
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

        try:
            response = self.get(url, headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://duckduckgo.com/",
            })

            if response.status_code in (403, 429):
                return SourceResult(
                    self.source_id,
                    self.source_name,
                    self.source_type,
                    self.reliability,
                    "blocked",
                    warnings=[f"DuckDuckGo returned HTTP {response.status_code}."],
                    error=response.text[:180],
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    searched_url=url,
                )

            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            items = []

            for result in soup.select(".result, .web-result")[:30]:
                title_el = result.select_one(".result__title, .result__a, a")
                snippet_el = result.select_one(".result__snippet, .result__body, .result__extras")
                link_el = result.select_one("a.result__a, a[href]")

                title = title_el.get_text(" ", strip=True) if title_el else profile.query
                snippet = snippet_el.get_text(" ", strip=True) if snippet_el else result.get_text(" ", strip=True)
                href = link_el.get("href", "") if link_el else ""

                price = self._extract_price(f"{title} {snippet}")
                if not self.plausible_price(price, listing.get("priceValue"), profile=profile):
                    continue
                combined_text = f"{title} {snippet}"
                if profile.exact_match_required and not exact_model_match(combined_text, profile.must_have_tokens):
                    continue
                if excluded_model_match(combined_text, profile.excluded_tokens):
                    continue

                real_url = self._unwrap_ddg_url(href)

                item = self.make_item(
                    title=title,
                    price=price,
                    url=real_url or href or "",
                    profile=profile,
                    listing_price=listing.get("priceValue"),
                )
                item.source_type = self.source_type
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
                warnings=[] if items else ["Search fallback found no snippets with usable prices."],
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                searched_url=url,
            )
        except Exception as exc:
            result = self.result_from_exception(exc, url)
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return result

    def _extract_price(self, text):
        m = PRICE_EUR_RE.search(text or "")
        if not m:
            return None
        return parse_euro_price(m.group(1) or m.group(2))

    def _unwrap_ddg_url(self, href):
        if not href:
            return ""
        if href.startswith("//"):
            href = "https:" + href
        try:
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
        except Exception:
            pass
        return href
