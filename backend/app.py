import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

from dealradar.engine import DealRadarEngine
from dealradar.settings import load_settings

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

settings = load_settings()
engine = DealRadarEngine(settings)

API_TOKEN = os.getenv("DEAL_RADAR_API_TOKEN", "").strip()


def require_auth():
    if not API_TOKEN:
        return None

    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {API_TOKEN}":
        return jsonify({"error": "Unauthorized"}), 401

    return None


@app.get("/")
def home():
    return jsonify({
        "status": "ok",
        "name": "OLX Deal Radar Backend v4",
        "endpoints": {
            "health": "/health",
            "evaluate": "/api/evaluate",
            "sources": "/api/sources",
            "debug_query": "/api/debug-query?title=RTX%203080%20Ti"
        }
    })


@app.get("/health")
def health():
    return jsonify({"status": "healthy"})


@app.get("/api/sources")
def sources():
    auth_error = require_auth()
    if auth_error:
        return auth_error

    return jsonify(engine.describe_sources())


@app.get("/api/debug-query")
def debug_query():
    auth_error = require_auth()
    if auth_error:
        return auth_error

    title = request.args.get("title", "")
    return jsonify(engine.debug_query(title))


@app.post("/api/evaluate")
def evaluate():
    auth_error = require_auth()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True) or {}
    listing = payload.get("listing") or {}
    client_settings = payload.get("settings") or {}

    result = engine.evaluate(listing=listing, client_settings=client_settings)
    status = 200 if not result.get("fatalError") else 400
    return jsonify(result), status


@app.get("/api/test-fetch")
def test_fetch():
    auth_error = require_auth()
    if auth_error:
        return auth_error

    title = request.args.get("title", "RTX 3080")
    price = request.args.get("price", "360")
    try:
        price_value = float(price)
    except Exception:
        price_value = 360.0

    listing = {
        "title": title,
        "priceValue": price_value,
        "description": request.args.get("description", ""),
        "url": "debug://test-fetch",
    }
    result = engine.evaluate(listing=listing, client_settings={
        "minProfitPct": request.args.get("minProfitPct", 25),
        "minimumProfitEuro": request.args.get("minimumProfitEuro", 30),
        "feePct": request.args.get("feePct", 8),
        "mode": "resale",
    })

    return jsonify({
        "queryUsed": result.get("queryUsed"),
        "verdict": result.get("verdict"),
        "summary": result.get("summary"),
        "sampleSize": result.get("sampleSize"),
        "sources": [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "status": s.get("status"),
                "sampleSize": s.get("sampleSize"),
                "median": s.get("median"),
                "error": s.get("error"),
                "warnings": s.get("warnings"),
                "searchedUrl": s.get("searchedUrl"),
                "items": s.get("items", [])[:3],
            }
            for s in result.get("sources", [])
        ],
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True)
