const DEFAULTS = {
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

document.addEventListener("DOMContentLoaded", restore);
document.querySelector("#settings-form").addEventListener("submit", save);

async function restore() {
  const settings = await chrome.storage.local.get(DEFAULTS);
  for (const key of Object.keys(DEFAULTS)) {
    const el = document.getElementById(key);
    if (el) el.value = settings[key] ?? DEFAULTS[key];
  }
}

async function save(event) {
  event.preventDefault();
  const patch = {};
  for (const key of Object.keys(DEFAULTS)) {
    patch[key] = document.getElementById(key).value.trim();
  }
  await chrome.storage.local.set(patch);
  const status = document.getElementById("status");
  status.textContent = "Saved.";
  setTimeout(() => status.textContent = "", 1600);
}
