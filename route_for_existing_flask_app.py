"""
Drop-in route for your existing Flask dashboard/scraper.

Copy this into your current Flask app and replace `your_existing_evaluate_function`
with your actual scraper median-price function.
"""

import os
from flask import jsonify, request

DEAL_RADAR_API_TOKEN = os.getenv("DEAL_RADAR_API_TOKEN", "").strip()


def register_deal_radar_route(app):
    @app.post("/api/evaluate")
    def evaluate_from_extension():
        if DEAL_RADAR_API_TOKEN:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {DEAL_RADAR_API_TOKEN}":
                return jsonify({"error": "Unauthorized"}), 401

        payload = request.get_json(silent=True) or {}
        listing = payload.get("listing") or {}
        settings = payload.get("settings") or {}

        # Replace this with your real code:
        # result = your_existing_evaluate_function(
        #     title=listing["title"],
        #     seller_price=listing["priceValue"],
        #     description=listing.get("description", ""),
        #     location=listing.get("locationText", ""),
        #     min_profit_pct=settings.get("minProfitPct", 25),
        #     min_profit_eur=settings.get("minimumProfitEuro", 30),
        # )
        result = {
            "verdict": "UNKNOWN",
            "confidence": "low",
            "summary": "Route installed, but you still need to connect your existing scraper function.",
            "marketMedian": None,
            "sampleSize": 0,
            "estimatedSalePrice": None,
            "estimatedProfit": None,
            "profitPct": None,
            "warnings": ["Connect this route to your scraper's median-price logic."],
            "sources": [],
            "listing": listing
        }

        return jsonify(result)
