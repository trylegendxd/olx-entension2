import os
import re
import time
import random
import statistics
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

API_TOKEN = os.getenv("DEAL_RADAR_API_TOKEN", "").strip()
ENABLE_LIVE_SCRAPING = os.getenv("ENABLE_LIVE_SCRAPING", "0") == "1"

BAD_WORDS = [
    "avariado", "avariada", "danificado", "danificada", "defeito", "defeituoso",
    "defeituosa", "não funciona", "nao funciona", "partido", "partida", "reparar",
    "reparação", "reparacao", "peças", "pecas", "crash", "crasha", "sem garantia",
    "bloqueado", "bloqueada", "icloud", "para peças", "para pecas"
]

STOPWORDS = {
    "vendo", "troco", "novo", "nova", "usado", "usada", "selado", "selada",
    "excelente", "estado", "urgente", "preço", "preco", "negociável", "negociavel"
}


@dataclass
class SourceResult:
    id: str
    name: str
    prices: List[float]
    reliability: str

    @property
    def median(self) -> Optional[float]:
        cleaned = trim_outliers(self.prices)
        if not cleaned:
            return None
        return round(statistics.median(cleaned), 2)

    @property
    def sample_size(self) -> int:
        return len(self.prices)


@app.post("/api/evaluate")
def api_evaluate():
    if API_TOKEN:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {API_TOKEN}":
            return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    listing = payload.get("listing") or {}
    settings = payload.get("settings") or {}

    try:
        result = evaluate_listing(listing, settings)
        return jsonify(result)
    except Exception as exc:
        app.logger.exception("Evaluation failed")
        return jsonify({"error": str(exc)}), 500


def evaluate_listing(listing: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    title = str(listing.get("title") or listing.get("rawTitle") or "").strip()
    price = to_float(listing.get("priceValue"))
    description = str(listing.get("description") or "")
    risky_words = detect_risky_words(f"{title}\n{description}")

    if price is None:
        return unknown("Could not extract a valid listing price.", listing, risky_words)

    existing = evaluate_with_existing_scraper(listing, settings)
    if existing:
        return existing

    query = normalize_query(title)

    source_results: List[SourceResult] = []
    if ENABLE_LIVE_SCRAPING:
        source_results.append(scrape_ebay_sold(query))
        source_results.append(scrape_olx_active(query))
    else:
        source_results = []

    all_prices = []
    sources = []
    for source in source_results:
        cleaned = trim_outliers(source.prices)
        all_prices.extend(cleaned)
        sources.append({
            "id": source.id,
            "name": source.name,
            "median": source.median,
            "sampleSize": source.sample_size,
            "reliability": source.reliability
        })

    cleaned_market_prices = trim_outliers(all_prices)
    sample_size = len(cleaned_market_prices)

    if sample_size < 5:
        return {
            "verdict": "UNKNOWN",
            "confidence": "low",
            "summary": "Not enough comparable market data. Use the manual source links and check the exact model/condition.",
            "marketMedian": None,
            "sampleSize": sample_size,
            "estimatedSalePrice": None,
            "estimatedProfit": None,
            "profitPct": None,
            "warnings": build_warnings(risky_words, sample_size, title),
            "sources": sources,
            "listing": listing
        }

    market_median = round(statistics.median(cleaned_market_prices), 2)
    fee_pct = to_float(settings.get("feePct")) or 0
    tax_pct = to_float(settings.get("taxPct")) or 0
    min_profit_pct = to_float(settings.get("minProfitPct")) or 25
    minimum_profit_euro = to_float(settings.get("minimumProfitEuro")) or 30

    estimated_sale_after_costs = market_median * (1 - (fee_pct + tax_pct) / 100)
    estimated_profit = round(estimated_sale_after_costs - price, 2)
    profit_pct = round((estimated_profit / price) * 100, 1) if price > 0 else None
    below_market_pct = round((1 - price / market_median) * 100, 1) if market_median > 0 else 0

    confidence = confidence_from_sample(sample_size, cleaned_market_prices, market_median)
    verdict = decide_verdict(
        price=price,
        market_median=market_median,
        estimated_profit=estimated_profit,
        profit_pct=profit_pct or 0,
        min_profit_pct=min_profit_pct,
        minimum_profit_euro=minimum_profit_euro,
        risky_words=risky_words
    )

    summary = make_summary(verdict, below_market_pct, estimated_profit, sample_size)

    return {
        "verdict": verdict,
        "confidence": confidence,
        "summary": summary,
        "marketMedian": market_median,
        "sampleSize": sample_size,
        "estimatedSalePrice": market_median,
        "estimatedProfit": estimated_profit,
        "profitPct": profit_pct,
        "warnings": build_warnings(risky_words, sample_size, title),
        "sources": sources,
        "listing": listing
    }


def evaluate_with_existing_scraper(listing: Dict[str, Any], settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Plug your current OLX bot/scraper here.

    Example integration idea:

        from scraper import evaluate_market
        return evaluate_market(
            title=listing["title"],
            price=listing["priceValue"],
            location=listing.get("locationText"),
            settings=settings
        )

    Return None to use the simple fallback logic.
    """
    return None


def scrape_ebay_sold(query: str) -> SourceResult:
    url = f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(query)}&LH_Sold=1&LH_Complete=1"
    prices = scrape_price_regex(url, currency_hint="EUR_OR_USD")
    return SourceResult(
        id="ebay_sold",
        name="eBay Sold/Completed",
        prices=prices[:30],
        reliability="high"
    )


def scrape_olx_active(query: str) -> SourceResult:
    slug = quote_plus(query).replace("+", "-")
    url = f"https://www.olx.pt/items/q-{slug}/"
    prices = scrape_price_regex(url, currency_hint="EUR")
    return SourceResult(
        id="olx_pt",
        name="OLX Portugal active listings",
        prices=prices[:30],
        reliability="medium"
    )


def scrape_price_regex(url: str, currency_hint: str = "EUR") -> List[float]:
    polite_delay()
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; OLXDealRadar/1.0; +local-user-tool)",
        "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.7"
    }
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    prices = []

    # EUR: 1.234,56 € / 1234 € / €1234
    for match in re.finditer(r"(?:(?:€\s*)?(\d{1,5}(?:[.\s]\d{3})*(?:,\d{1,2})?)\s*€|€\s*(\d{1,5}(?:[,\s]\d{3})*(?:\.\d{1,2})?))", text):
        raw = match.group(1) or match.group(2)
        value = parse_price(raw)
        if value and 5 <= value <= 10000:
            prices.append(value)

    if currency_hint == "EUR_OR_USD":
        # USD fallback. Rough conversion deliberately not done here; for production,
        # use a proper FX source and cache the rate.
        for match in re.finditer(r"\$\s*(\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)", text):
            value = parse_price(match.group(1), decimal=".")
            if value and 5 <= value <= 10000:
                prices.append(value)

    return prices


def polite_delay():
    time.sleep(random.uniform(0.8, 1.8))


def normalize_query(title: str) -> str:
    text = re.sub(r"[^\w\s+\-.]", " ", title.lower(), flags=re.UNICODE)
    words = [w for w in text.split() if w not in STOPWORDS]
    return " ".join(words[:10]).strip()


def parse_price(raw: str, decimal: str = ",") -> Optional[float]:
    if not raw:
        return None

    raw = raw.strip()

    if decimal == ",":
        raw = raw.replace(".", "").replace(" ", "").replace(",", ".")
    else:
        raw = raw.replace(",", "").replace(" ", "")

    try:
        return float(raw)
    except ValueError:
        return None


def trim_outliers(values: List[float]) -> List[float]:
    values = sorted([float(v) for v in values if v is not None and v > 0])
    if len(values) < 4:
        return values

    q1 = percentile(values, 25)
    q3 = percentile(values, 75)
    iqr = q3 - q1
    if iqr <= 0:
        return values

    low = q1 - 1.5 * iqr
    high = q3 + 1.5 * iqr
    return [v for v in values if low <= v <= high]


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0
    k = (len(values) - 1) * (pct / 100)
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


def detect_risky_words(text: str) -> List[str]:
    found = []
    lower = text.lower()
    for word in BAD_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lower):
            found.append(word)
    return sorted(set(found))


def decide_verdict(
    price: float,
    market_median: float,
    estimated_profit: float,
    profit_pct: float,
    min_profit_pct: float,
    minimum_profit_euro: float,
    risky_words: List[str]
) -> str:
    if risky_words:
        return "AVOID"

    if estimated_profit >= minimum_profit_euro and profit_pct >= min_profit_pct and price <= market_median * 0.78:
        return "RESALE_BUY"

    if price <= market_median * 0.86:
        return "GOOD_DEAL"

    if price <= market_median * 1.08:
        return "FAIR"

    return "AVOID"


def confidence_from_sample(sample_size: int, prices: List[float], median: float) -> str:
    if sample_size < 5:
        return "low"

    spread = statistics.pstdev(prices) / median if median else 1

    if sample_size >= 15 and spread < 0.25:
        return "high"

    if sample_size >= 8 and spread < 0.38:
        return "medium"

    return "low"


def make_summary(verdict: str, below_market_pct: float, estimated_profit: float, sample_size: int) -> str:
    if verdict == "RESALE_BUY":
        return f"Looks resellable: about {below_market_pct:.1f}% below market median, estimated profit €{estimated_profit:.0f} from {sample_size} comps."
    if verdict == "GOOD_DEAL":
        return f"Looks like a good deal: about {below_market_pct:.1f}% below market median, but resale margin may be limited."
    if verdict == "FAIR":
        return f"Fair price: close to market median based on {sample_size} comparable prices."
    if verdict == "AVOID":
        return "Avoid or inspect carefully: price/risk does not justify the deal."
    return "Not enough evidence to decide."


def build_warnings(risky_words: List[str], sample_size: int, title: str) -> List[str]:
    warnings = []

    if risky_words:
        warnings.append(f"Risk words found: {', '.join(risky_words)}.")

    if sample_size < 8:
        warnings.append("Small comparable sample. Confirm manually before buying.")

    if len(normalize_query(title).split()) < 3:
        warnings.append("Title is too vague; exact model matching may be weak.")

    warnings.append("Always confirm condition, warranty, serial/model, and seller history before paying.")

    return warnings


def unknown(reason: str, listing: Dict[str, Any], risky_words: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "verdict": "UNKNOWN",
        "confidence": "low",
        "summary": reason,
        "marketMedian": None,
        "sampleSize": 0,
        "estimatedSalePrice": None,
        "estimatedProfit": None,
        "profitPct": None,
        "warnings": build_warnings(risky_words or [], 0, str(listing.get("title") or "")),
        "sources": [],
        "listing": listing
    }


def to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=True)
