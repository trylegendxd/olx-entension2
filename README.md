# OLX Deal Radar Smart Repo

Complete repo with:

- `extension/` — Chrome/Edge Manifest V3 extension.
- `backend/` — Flask backend for Render/local deployment.
- Multi-source marketplace price fetching.
- Smarter query cleaning and model extraction.
- Per-source error isolation, so eBay/Wallapop/etc. blocking does not kill the whole evaluation.
- Caching, outlier trimming, similarity filtering, risky-word detection, and resale scoring.

## Architecture

```txt
OLX listing page
    ↓
Browser extension content script extracts listing data
    ↓
Extension background worker sends POST /api/evaluate
    ↓
Flask backend searches multiple marketplaces
    ↓
Backend returns verdict + median + profit + source breakdown
    ↓
Extension overlay displays the decision
```

## Backend sources included

Enabled fetchers:

- OLX Portugal active listings
- CustoJusto active listings
- Wallapop search
- KuantoKusta retail reference
- Worten retail reference
- eBay Browse API active listings, if credentials are configured
- SerpApi eBay sold listings, if `SERPAPI_KEY` is configured
- eBay HTML fallback, best effort only and safely ignored if blocked

Important: some marketplaces block automated requests. This backend is designed to **try multiple sources**, keep working when one fails, and tell you which sources succeeded/failed.

## Deploy backend to Render

Render should use the `backend` folder as root.

Settings:

```txt
Root Directory: backend
Build Command: pip install -r requirements.txt
Start Command: gunicorn app:app --bind 0.0.0.0:$PORT
```

Environment variables:

```txt
DEAL_RADAR_API_TOKEN=choose-a-secret
ENABLE_WEB_SOURCES=1
MAX_WORKERS=5
CACHE_TTL_SECONDS=900
```

Optional API variables:

```txt
SERPAPI_KEY=your-serpapi-key
EBAY_CLIENT_ID=your-ebay-client-id
EBAY_CLIENT_SECRET=your-ebay-client-secret
EBAY_MARKETPLACE_ID=EBAY_ES
```

After deploy, your extension backend URL is:

```txt
https://your-render-service.onrender.com/api/evaluate
```

## Install extension

1. Go to `chrome://extensions`
2. Enable Developer Mode.
3. Click **Load unpacked**.
4. Select the `extension` folder.
5. Open extension Options.
6. Put your backend URL:
   `https://your-render-service.onrender.com/api/evaluate`
7. Put the same API token as `DEAL_RADAR_API_TOKEN`.

## Local backend test

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then set extension backend URL to:

```txt
http://127.0.0.1:5000/api/evaluate
```

## Test endpoint

```bash
curl -X POST http://127.0.0.1:5000/api/evaluate ^
  -H "Content-Type: application/json" ^
  -d "{\"listing\":{\"title\":\"RTX 3080 Ti\",\"priceValue\":360,\"description\":\"como nova\",\"url\":\"https://olx.pt/test\"}}"
```
