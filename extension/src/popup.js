document.addEventListener("DOMContentLoaded", async () => {
  const { lastEvaluation } = await chrome.storage.local.get(["lastEvaluation"]);
  const el = document.getElementById("content");

  if (!lastEvaluation) {
    el.textContent = "Open an OLX announcement page to scan a listing.";
  } else {
    const verdict = lastEvaluation.verdict || "UNKNOWN";
    const cls = verdict === "RESALE_BUY" || verdict === "GOOD_DEAL" ? "good" : verdict === "AVOID" ? "bad" : "neutral";
    el.innerHTML = `
      <strong class="${cls}">${escapeHtml(verdict)}</strong>
      <div>${escapeHtml(lastEvaluation.summary || "No summary.")}</div>
      <div class="muted">Query: ${escapeHtml(lastEvaluation.queryUsed || "—")}</div>
    `;
  }

  document.getElementById("options").addEventListener("click", () => chrome.runtime.openOptionsPage());
});

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
