const DEFAULT_SETTINGS = {
  apiUrl: "",
  apiToken: "",
  minProfitPct: 25,
  minimumProfitEuro: 30,
  preferredRegion: "Braga",
  maxDistanceKm: "",
  feePct: 8,
  taxPct: 0,
  mode: "resale"
};

chrome.runtime.onInstalled.addListener(async () => {
  const current = await chrome.storage.local.get(Object.keys(DEFAULT_SETTINGS));
  const patch = {};
  for (const [key, value] of Object.entries(DEFAULT_SETTINGS)) {
    if (current[key] === undefined) patch[key] = value;
  }
  if (Object.keys(patch).length) await chrome.storage.local.set(patch);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== "EVALUATE_OLX_LISTING") return false;

  evaluate(message.payload)
    .then((result) => sendResponse({ ok: true, result }))
    .catch((error) => sendResponse({ ok: false, error: error?.message || String(error) }));

  return true;
});

async function evaluate(listing) {
  const settings = await chrome.storage.local.get(DEFAULT_SETTINGS);

  if (!settings.apiUrl) {
    return {
      verdict: "UNKNOWN",
      confidence: "low",
      summary: "Backend not configured. Open extension options and add your Render /api/evaluate URL.",
      listing,
      warnings: ["Backend URL missing."],
      sources: [],
      manualLinks: buildManualLinks(listing.title || "")
    };
  }

  const body = {
    listing,
    settings: {
      minProfitPct: Number(settings.minProfitPct || 25),
      minimumProfitEuro: Number(settings.minimumProfitEuro || 30),
      preferredRegion: settings.preferredRegion || "Braga",
      maxDistanceKm: settings.maxDistanceKm ? Number(settings.maxDistanceKm) : null,
      feePct: Number(settings.feePct || 0),
      taxPct: Number(settings.taxPct || 0),
      mode: settings.mode || "resale"
    }
  };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 35000);

  try {
    const response = await fetch(settings.apiUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(settings.apiToken ? { Authorization: `Bearer ${settings.apiToken}` } : {})
      },
      body: JSON.stringify(body),
      signal: controller.signal
    });

    const text = await response.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(`Backend returned non-JSON response: ${text.slice(0, 180).replace(/\s+/g, " ")}`);
    }

    if (!response.ok) {
      throw new Error(data?.error || data?.summary || `Backend error ${response.status}`);
    }

    await chrome.storage.local.set({ lastEvaluation: data });
    return data;
  } finally {
    clearTimeout(timer);
  }
}

function buildManualLinks(query) {
  const q = encodeURIComponent(String(query || "").slice(0, 100));
  const slug = String(query || "").toLowerCase().replace(/[^\w]+/g, "-").replace(/^-+|-+$/g, "");
  return [
    { name: "OLX", url: `https://www.olx.pt/items/q-${slug}/` },
    { name: "CustoJusto", url: `https://www.custojusto.pt/portugal?q=${q}` },
    { name: "Wallapop", url: `https://pt.wallapop.com/search?keywords=${q}` },
    { name: "KuantoKusta", url: `https://www.kuantokusta.pt/search?q=${q}` },
    { name: "eBay sold", url: `https://www.ebay.com/sch/i.html?_nkw=${q}&LH_Sold=1&LH_Complete=1` }
  ];
}
