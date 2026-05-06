# OLX Deal Radar Smart Repo v4

This version fixes the nonsense result where one bad parsed price like `18 500 €` could become the market median for a GPU.

## Critical fixes in v4

- **No median/profit from 1-2 items.**
  - Backend now needs at least `min_comparable_items` used-market comps before showing used median/profit.
- **Category-specific price sanity bounds.**
  - Example: `RTX 3060 Ti` comps above roughly `480 €` are rejected.
  - So `18 500 €` can no longer be accepted as a GPU comparison.
- **Exact model matching for known products.**
  - `RTX 3060 Ti` must contain `rtx`, `3060`, and `ti`.
  - It will not compare against `RTX 3060`, `RTX 3070`, laptops, rigs, or random expensive listings.
- **Regex fallback is now strict.**
  - It no longer invents `title = query` around random prices.
  - The price context must contain the exact model tokens.
- **Retail is reference only.**
  - Used-market valuation is not calculated from KuantoKusta/Worten retail prices alone.
- **Better low-confidence behavior.**
  - If only 1 item is found, result is `UNKNOWN`, not fake profit.

## Render setup

Use:

```txt
Root Directory: backend
Build Command: pip install -r requirements.txt
Start Command: gunicorn app:app --bind 0.0.0.0:$PORT
```

Env vars:

```txt
DEAL_RADAR_API_TOKEN=your-token
ENABLE_WEB_SOURCES=1
MAX_WORKERS=7
CACHE_TTL_SECONDS=900
```

Optional but recommended for reliable eBay/sold data:

```txt
SERPAPI_KEY=your-serpapi-key
EBAY_CLIENT_ID=your-ebay-client-id
EBAY_CLIENT_SECRET=your-ebay-client-secret
EBAY_MARKETPLACE_ID=EBAY_ES
```

## Extension setup

Load this folder in Chrome/Edge:

```txt
extension
```

Then set backend URL:

```txt
https://your-render-app.onrender.com/api/evaluate
```

## Debug

Test this:

```txt
https://your-render-app.onrender.com/api/test-fetch?title=RTX%203060%20Ti&price=320
```

With token:

```bash
curl "https://your-render-app.onrender.com/api/test-fetch?title=RTX%203060%20Ti&price=320" ^
  -H "Authorization: Bearer YOUR_TOKEN"
```

Expected behavior:

- If fewer than 3 used comps are found: `UNKNOWN`.
- No fake `18 500 €` median.
- Sources can still show `blocked`/`empty`, but they should not poison the verdict.
