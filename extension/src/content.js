(() => {
  const PANEL_ID = "olx-deal-radar-panel-v2";
  const BAD_WORDS = [
    "avariado", "avariada", "danificado", "danificada", "defeito", "defeituoso",
    "defeituosa", "não funciona", "nao funciona", "partido", "partida", "reparar",
    "reparação", "reparacao", "peças", "pecas", "para peças", "para pecas",
    "crash", "crasha", "sem garantia", "bloqueado", "bloqueada", "icloud"
  ];

  let lastSignature = "";
  let timer = null;

  init();

  function init() {
    ensurePanel();
    scheduleScan();

    new MutationObserver(() => scheduleScan()).observe(document.documentElement, {
      subtree: true,
      childList: true,
      characterData: true
    });

    window.addEventListener("popstate", scheduleScan);
    window.addEventListener("hashchange", scheduleScan);
  }

  function scheduleScan() {
    clearTimeout(timer);
    timer = setTimeout(scan, 900);
  }

  async function scan() {
    const listing = extractListing();
    if (!listing.title || listing.priceValue == null) return;

    const signature = `${location.href}|${listing.title}|${listing.priceValue}`;
    if (signature === lastSignature) return;
    lastSignature = signature;

    renderLoading(listing);

    try {
      const response = await chrome.runtime.sendMessage({
        type: "EVALUATE_OLX_LISTING",
        payload: listing
      });

      if (!response?.ok) throw new Error(response?.error || "Evaluation failed");
      renderResult(response.result);
    } catch (error) {
      renderError(error, listing);
    }
  }

  function extractListing() {
    const rawTitle =
      text('h1') ||
      meta("og:title") ||
      document.title.replace(/\s*[-|]\s*OLX.*$/i, "").trim();

    const title = cleanTitle(rawTitle);
    const description = findDescription();
    const pageText = document.body.innerText || "";
    const priceText = findPriceText();
    const priceValue = parseEuro(priceText);
    const locationText = findLocation(pageText);
    const sellerName = findSeller();
    const imageUrl = meta("og:image") || firstImage();
    const dateText = findDate(pageText);

    const riskyWords = BAD_WORDS.filter((word) =>
      new RegExp(`\\b${escapeRegex(word)}\\b`, "i").test(`${title}\n${description}`)
    );

    return {
      source: "olx.pt",
      url: location.href,
      title,
      rawTitle,
      priceText,
      priceValue,
      currency: priceValue != null ? "EUR" : null,
      locationText,
      sellerName,
      description,
      imageUrl,
      dateText,
      riskyWords,
      extractedAt: new Date().toISOString()
    };
  }

  function cleanTitle(value) {
    return String(value || "")
      .replace(/\s*[-|]\s*OLX Portugal.*$/i, "")
      .replace(/\s*[-|]\s*OLX.*$/i, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function findDescription() {
    const selectors = [
      '[data-cy="ad_description"]',
      '[data-testid="ad-description"]',
      'section [data-testid*="description"]'
    ];

    for (const selector of selectors) {
      const value = text(selector);
      if (value && value.length >= 20) return value.slice(0, 5000);
    }

    const metaDesc = meta("description");
    if (metaDesc) return metaDesc.slice(0, 5000);

    const candidates = [...document.querySelectorAll("p, div")]
      .map((el) => el.innerText?.trim())
      .filter((t) => t && t.length > 100 && t.length < 5000)
      .sort((a, b) => b.length - a.length);

    return (candidates[0] || "").slice(0, 5000);
  }

  function findPriceText() {
    const selectors = [
      '[data-testid="ad-price"]',
      '[data-cy="ad-price"]',
      '[aria-label*="preço" i]',
      '[aria-label*="price" i]',
      'h2',
      'h3'
    ];

    for (const selector of selectors) {
      const value = text(selector);
      if (value && /€/.test(value) && parseEuro(value) != null) return compact(value);
    }

    const candidates = [...document.querySelectorAll("h1,h2,h3,h4,span,p,div")]
      .map((el) => el.innerText?.trim())
      .filter(Boolean)
      .filter((value) => value.length <= 90 && /€/.test(value))
      .map((value) => ({ value, price: parseEuro(value) }))
      .filter((x) => x.price != null)
      .sort((a, b) => a.value.length - b.value.length);

    return candidates[0]?.value || "";
  }

  function parseEuro(value) {
    if (!value) return null;
    const match = String(value).match(/(\d{1,3}(?:[.\s]\d{3})*|\d+)(?:,(\d{1,2}))?\s*€/);
    if (!match) return null;
    const whole = match[1].replace(/[.\s]/g, "");
    const cents = match[2] || "00";
    const n = Number(`${whole}.${cents.padEnd(2, "0")}`);
    return Number.isFinite(n) ? n : null;
  }

  function findLocation(pageText) {
    const selectors = [
      '[data-testid="location-date"]',
      '[data-cy="ad-location"]',
      '[aria-label*="Localização" i]',
      '[aria-label*="location" i]'
    ];

    for (const selector of selectors) {
      const value = text(selector);
      if (value && value.length < 180) return compact(value);
    }

    const districts = ["Braga", "Porto", "Lisboa", "Viana do Castelo", "Aveiro", "Coimbra", "Faro", "Setúbal"];
    return districts.find((d) => pageText.includes(d)) || "";
  }

  function findSeller() {
    const selectors = [
      '[data-testid="user-profile-link"]',
      '[data-testid*="seller"]',
      'a[href*="/perfil/"]',
      'a[href*="/ads/user/"]'
    ];

    for (const selector of selectors) {
      const value = text(selector);
      if (value && value.length < 90) return compact(value);
    }
    return "";
  }

  function findDate(pageText) {
    const match = pageText.match(/(Hoje|Ontem|\d{1,2}\s+de\s+\p{L}+\s+às\s+\d{1,2}:\d{2}|\d{1,2}\/\d{1,2}\/\d{2,4})/iu);
    return match?.[0] || "";
  }

  function firstImage() {
    const img = [...document.images].find((el) => el.src && el.width > 180 && el.height > 120);
    return img?.src || "";
  }

  function text(selector) {
    return document.querySelector(selector)?.innerText?.trim() || "";
  }

  function meta(name) {
    return document.querySelector(`meta[property="${name}"], meta[name="${name}"]`)?.content?.trim() || "";
  }

  function compact(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function escapeRegex(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function ensurePanel() {
    let panel = document.getElementById(PANEL_ID);
    if (panel) return panel;

    panel = document.createElement("aside");
    panel.id = PANEL_ID;
    panel.innerHTML = `
      <div class="odr-header">
        <div>
          <strong>OLX Deal Radar</strong>
          <span>multi-source resale scanner</span>
        </div>
        <button class="odr-close" title="Minimize">×</button>
      </div>
      <div class="odr-body"></div>
    `;

    document.documentElement.appendChild(panel);
    panel.querySelector(".odr-close").addEventListener("click", () => {
      panel.classList.toggle("odr-minimized");
    });

    return panel;
  }

  function renderLoading(listing) {
    const panel = ensurePanel();
    panel.classList.remove("odr-minimized");
    panel.querySelector(".odr-body").innerHTML = `
      <div class="odr-listing">
        ${listing.imageUrl ? `<img src="${escapeHtml(listing.imageUrl)}" alt="">` : ""}
        <div>
          <div class="odr-title">${escapeHtml(listing.title)}</div>
          <div class="odr-muted">${escapeHtml(listing.priceText || "No price")} ${listing.locationText ? "• " + escapeHtml(listing.locationText) : ""}</div>
        </div>
      </div>
      <div class="odr-spinner-row"><div class="odr-spinner"></div><span>Fetching prices from multiple sources...</span></div>
    `;
  }

  function renderError(error, listing) {
    const panel = ensurePanel();
    panel.querySelector(".odr-body").innerHTML = `
      <div class="odr-listing">
        ${listing.imageUrl ? `<img src="${escapeHtml(listing.imageUrl)}" alt="">` : ""}
        <div>
          <div class="odr-title">${escapeHtml(listing.title)}</div>
          <div class="odr-muted">${escapeHtml(listing.priceText || "")}</div>
        </div>
      </div>
      <div class="odr-alert odr-alert-bad">${escapeHtml(error?.message || "Evaluation failed")}</div>
      <button class="odr-btn" id="odr-options">Open options</button>
    `;

    panel.querySelector("#odr-options")?.addEventListener("click", () => chrome.runtime.openOptionsPage());
  }

  function renderResult(result) {
    const panel = ensurePanel();
    const sources = Array.isArray(result.sources) ? result.sources : [];
    const warnings = Array.isArray(result.warnings) ? result.warnings : [];
    const links = Array.isArray(result.manualLinks) ? result.manualLinks : [];

    panel.querySelector(".odr-body").innerHTML = `
      <div class="odr-verdict ${verdictClass(result.verdict)}">
        <span>${escapeHtml(label(result.verdict))}</span>
        <small>${escapeHtml(result.confidence || "low")} confidence</small>
      </div>

      <p class="odr-summary">${escapeHtml(result.summary || "")}</p>

      <div class="odr-query">Query used: <strong>${escapeHtml(result.queryUsed || "—")}</strong></div>

      <div class="odr-metrics">
        ${metric("Used median", money(result.usedMarketMedian))}
        ${metric("Retail median", money(result.retailMedian))}
        ${metric("Profit", money(result.estimatedProfit))}
        ${metric("Margin", pct(result.profitPct))}
        ${metric("Used samples", result.usedSampleSize ?? "—")}
        ${metric("Below market", pct(result.belowMarketPct))}
      </div>

      ${warnings.length ? `<div class="odr-warnings">${warnings.slice(0, 5).map(w => `<div>⚠ ${escapeHtml(w)}</div>`).join("")}</div>` : ""}

      <h4>Sources</h4>
      <div class="odr-sources">
        ${sources.map(sourceCard).join("") || `<div class="odr-source"><strong>No sources returned</strong><span>Check backend logs.</span></div>`}
      </div>

      <h4>Manual checks</h4>
      <div class="odr-links">${links.map(l => `<a href="${escapeHtml(l.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(l.name)}</a>`).join("")}</div>
    `;
  }

  function sourceCard(source) {
    const statusClass = source.status === "ok" ? "ok" : source.status === "blocked" || source.status === "error" ? "bad" : "muted";
    const info = [
      source.status,
      source.sampleSize != null ? `${source.sampleSize} items` : "",
      source.median != null ? `median ${money(source.median)}` : "",
      source.error ? String(source.error).slice(0, 70) : ""
    ].filter(Boolean).join(" • ");

    return `
      <div class="odr-source">
        <strong>${escapeHtml(source.name || source.id)}</strong>
        <span class="${statusClass}">${escapeHtml(info)}</span>
      </div>
    `;
  }

  function metric(name, value) {
    return `<div><span>${escapeHtml(name)}</span><strong>${escapeHtml(value)}</strong></div>`;
  }

  function verdictClass(verdict) {
    return {
      RESALE_BUY: "odr-good",
      GOOD_DEAL: "odr-good",
      FAIR: "odr-neutral",
      AVOID: "odr-bad",
      UNKNOWN: "odr-unknown"
    }[verdict] || "odr-unknown";
  }

  function label(verdict) {
    return {
      RESALE_BUY: "Buy for resale",
      GOOD_DEAL: "Good deal",
      FAIR: "Fair price",
      AVOID: "Avoid",
      UNKNOWN: "Unknown"
    }[verdict] || verdict || "Unknown";
  }

  function money(value) {
    if (value == null || value === "" || Number.isNaN(Number(value))) return "—";
    return new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(Number(value));
  }

  function pct(value) {
    if (value == null || value === "" || Number.isNaN(Number(value))) return "—";
    return `${Number(value).toFixed(1)}%`;
  }
})();
