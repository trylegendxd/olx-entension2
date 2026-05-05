# Example backend

This is a minimal Flask backend for the OLX Deal Radar extension.

It gives you:

- `/api/evaluate`
- optional bearer token auth through `DEAL_RADAR_API_TOKEN`
- conservative scoring logic
- optional live HTML scraping attempts for eBay sold and OLX active listings

## Run locally

```bash
cd backend_example
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then set the extension backend URL to:

```txt
http://127.0.0.1:5000/api/evaluate
```

## Optional environment variables

```bash
export DEAL_RADAR_API_TOKEN="your-secret-token"
export ENABLE_LIVE_SCRAPING="1"
```

If `ENABLE_LIVE_SCRAPING` is not `1`, the backend returns a low-confidence `UNKNOWN` verdict unless you connect your existing scraper.

## Best production setup

Use your existing OLX bot/scraper logic inside `evaluate_with_existing_scraper()`. The placeholder is already in `app.py`.
