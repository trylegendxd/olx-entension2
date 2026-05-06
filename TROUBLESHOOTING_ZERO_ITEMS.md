# Troubleshooting: all sources show 0 items

If the extension shows 0 items on all marketplaces, check this order.

## 1. Confirm backend is updated

Open:

```txt
https://your-render-app.onrender.com/
```

It should say `OLX Deal Radar Backend`.

Then open:

```txt
https://your-render-app.onrender.com/api/sources
```

If you use `DEAL_RADAR_API_TOKEN`, open this endpoint with curl/Postman using the Authorization header.

## 2. Use the debug fetch endpoint

Open:

```txt
https://your-render-app.onrender.com/api/test-fetch?title=RTX%203080&price=360
```

If token is enabled, use curl:

```bash
curl "https://your-render-app.onrender.com/api/test-fetch?title=RTX%203080&price=360" ^
  -H "Authorization: Bearer YOUR_TOKEN"
```

Look at each source:

- `ok` means items were parsed.
- `empty` means request worked but no usable items were parsed.
- `blocked` means the marketplace blocked Render.
- `error` means a request/parsing issue.

## 3. Render IPs get blocked

This is normal. Marketplaces often block datacenter traffic.

v3 tries:

- API-style endpoints
- embedded JSON
- HTML cards
- regex price extraction
- DuckDuckGo fallback
- optional eBay/SerpApi APIs

But no scraper can guarantee every marketplace works forever without official APIs.

## 4. Recommended reliable setup

For real reliability, set at least one paid/official API:

```txt
SERPAPI_KEY=...
```

or:

```txt
EBAY_CLIENT_ID=...
EBAY_CLIENT_SECRET=...
```

The backend will still use free sources first, but official/API sources improve sample size a lot.

## 5. Query too narrow

If `Query used` is too narrow, test:

```txt
/api/debug-query?title=YOUR_TITLE_HERE
```

For GPUs, the title should become something like:

```txt
rtx 3080
rtx 3080 ti
rtx 4070 ti
```

not a full OLX title with location/junk.
