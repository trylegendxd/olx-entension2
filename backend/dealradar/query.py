import re
from typing import List, Optional

from .models import ProductProfile
from .text import BRANDS, NOISE_WORDS, clean_text, normalize_space, strip_accents, tokenize


GPU_PATTERNS = [
    r"\b(?:nvidia\s*)?(rtx|gtx)\s*[- ]?(\d{3,4})\s*[- ]?(ti|super)?\b",
    r"\b(?:nvidia\s*)?(rtx|gtx)(\d{3,4})(ti|super)?\b",
    r"\b(?:amd\s*)?(rx)\s*[- ]?(\d{3,4})\s*[- ]?(xtx|xt)?\b",
    r"\b(?:amd\s*)?(rx)(\d{3,4})(xtx|xt)?\b",
]

IPHONE_RE = r"\biphone\s*(\d{1,2}|se|xr|xs)\s*(pro max|pro|plus|max|mini)?\b"
MACBOOK_RE = r"\bmacbook\s*(air|pro)?\s*(m[1-5])?\s*(\d{2})?\s*(?:gb)?\s*(\d{3,4})?\s*(?:gb|tb)?\b"
PLAYSTATION_RE = r"\b(?:sony\s*)?(ps5|playstation\s*5|ps4|playstation\s*4)(?:\s*(slim|pro|digital|disc))?\b"
XBOX_RE = r"\b(?:xbox)\s*(series\s*[sx]|one\s*[sx]?|360)\b"
NINTENDO_RE = r"\b(?:nintendo\s*)?(switch)(?:\s*(oled|lite))?\b"


GPU_PRICE_BOUNDS = {
    # deliberately broad used-market sanity limits in EUR
    "gtx 1060": (40, 160),
    "gtx 1070": (60, 220),
    "gtx 1080": (80, 300),
    "gtx 1080 ti": (120, 390),
    "rtx 2060": (90, 260),
    "rtx 2070": (120, 330),
    "rtx 2070 super": (140, 380),
    "rtx 2080": (150, 430),
    "rtx 2080 ti": (220, 620),
    "rtx 3050": (90, 260),
    "rtx 3060": (140, 360),
    "rtx 3060 ti": (180, 480),
    "rtx 3070": (220, 560),
    "rtx 3070 ti": (250, 620),
    "rtx 3080": (320, 850),
    "rtx 3080 ti": (390, 980),
    "rtx 3090": (550, 1250),
    "rtx 3090 ti": (650, 1500),
    "rtx 4060": (180, 420),
    "rtx 4060 ti": (240, 560),
    "rtx 4070": (380, 760),
    "rtx 4070 super": (450, 900),
    "rtx 4070 ti": (550, 1100),
    "rtx 4080": (800, 1500),
    "rtx 4080 super": (850, 1600),
    "rtx 4090": (1300, 2600),
    "rx 6600": (110, 280),
    "rx 6700 xt": (180, 430),
    "rx 6800": (250, 570),
    "rx 6800 xt": (300, 680),
    "rx 6900 xt": (360, 800),
    "rx 7900 xt": (550, 1100),
    "rx 7900 xtx": (700, 1350),
}


class QueryBuilder:
    def build(self, title: str, description: str = "") -> ProductProfile:
        raw_title = normalize_space(title or "")
        title_clean = clean_text(raw_title)
        lower = strip_accents(title_clean.lower())

        condition_hint = self._condition_hint(f"{title}\n{description}")

        for builder in [
            self._gpu_profile,
            self._iphone_profile,
            self._macbook_profile,
            self._playstation_profile,
            self._xbox_profile,
            self._nintendo_profile,
            self._generic_profile,
        ]:
            profile = builder(raw_title, title_clean, lower, condition_hint)
            if profile:
                return profile

        return self._generic_profile(raw_title, title_clean, lower, condition_hint)

    def _condition_hint(self, value: str) -> str:
        lower = strip_accents((value or "").lower())
        if re.search(r"\b(avariado|danificado|nao funciona|pecas|defeito|partido|reparar)\b", lower):
            return "for_parts"
        if re.search(r"\b(mineracao|mining|minado|minada|minar)\b", lower):
            return "mining_risk"
        if re.search(r"\b(novo|selado|selada|garantia)\b", lower):
            return "new_or_like_new"
        if re.search(r"\b(usado|usada)\b", lower):
            return "used"
        return "unknown"

    def _gpu_profile(self, raw: str, clean: str, lower: str, condition_hint: str) -> Optional[ProductProfile]:
        for pattern in GPU_PATTERNS:
            m = re.search(pattern, lower)
            if not m:
                continue

            family, number = m.group(1), m.group(2)
            suffix = (m.group(3) or "").strip()
            canonical = " ".join(x for x in [family, number, suffix] if x)
            canonical = normalize_space(canonical)

            min_price, max_price = GPU_PRICE_BOUNDS.get(canonical, (40, 3000))

            brand = self._find_brand(lower)

            # For GPUs, the model number must match. If suffix exists in the listing,
            # suffix must also match. This prevents RTX 3060 Ti being compared to
            # RTX 3060, RTX 3070, laptops, mining rigs, or unrelated expensive items.
            must_have = [family, number]
            if suffix:
                must_have.append(suffix)

            excluded = []
            if suffix == "ti":
                # Avoid non-Ti listings being accepted as Ti.
                excluded.append(f"{family} {number} super")
            elif suffix == "super":
                excluded.append(f"{family} {number} ti")

            return ProductProfile(
                raw_title=raw,
                clean_title=clean,
                query=canonical,
                category="gpu",
                brand=brand,
                canonical_model=canonical,
                model_tokens=must_have,
                must_have_tokens=must_have,
                excluded_tokens=excluded,
                condition_hint=condition_hint,
                min_price=min_price,
                max_price=max_price,
                exact_match_required=True,
            )
        return None

    def _iphone_profile(self, raw: str, clean: str, lower: str, condition_hint: str) -> Optional[ProductProfile]:
        m = re.search(IPHONE_RE, lower)
        if not m:
            return None
        number = m.group(1)
        variant = normalize_space(m.group(2) or "")
        storage = self._extract_storage(lower)
        query = normalize_space(" ".join(x for x in ["iphone", number, variant, storage] if x))
        must_have = [t for t in ["iphone", number] + variant.split() if t]
        return ProductProfile(
            raw, clean, query, "phone", "apple", query, must_have, must_have, [], condition_hint,
            min_price=40, max_price=2500, exact_match_required=True
        )

    def _macbook_profile(self, raw: str, clean: str, lower: str, condition_hint: str) -> Optional[ProductProfile]:
        m = re.search(MACBOOK_RE, lower)
        if not m or "macbook" not in lower:
            return None
        kind = m.group(1) or ""
        chip = m.group(2) or ""
        storage = self._extract_storage(lower)
        ram = ""
        ram_match = re.search(r"\b(8|16|18|24|32|36|48|64)\s*gb\b", lower)
        if ram_match:
            ram = f"{ram_match.group(1)}gb"
        query = normalize_space(" ".join(x for x in ["macbook", kind, chip, ram, storage] if x))
        must_have = [t for t in ["macbook", kind, chip] if t]
        return ProductProfile(
            raw, clean, query, "laptop", "apple", query, must_have, must_have, [], condition_hint,
            min_price=100, max_price=4000, exact_match_required=True
        )

    def _playstation_profile(self, raw: str, clean: str, lower: str, condition_hint: str) -> Optional[ProductProfile]:
        m = re.search(PLAYSTATION_RE, lower)
        if not m:
            return None
        model = m.group(1).replace("playstation 5", "ps5").replace("playstation 4", "ps4").replace(" ", "")
        variant = (m.group(2) or "").strip()
        query = normalize_space(" ".join([model, variant]))
        must_have = [model]
        return ProductProfile(
            raw, clean, query, "console", "sony", query, must_have, must_have, [], condition_hint,
            min_price=50, max_price=900, exact_match_required=True
        )

    def _xbox_profile(self, raw: str, clean: str, lower: str, condition_hint: str) -> Optional[ProductProfile]:
        m = re.search(XBOX_RE, lower)
        if not m:
            return None
        model = normalize_space(m.group(1).replace("series", "series "))
        query = f"xbox {model}"
        must_have = ["xbox"] + model.split()
        return ProductProfile(
            raw, clean, query, "console", "microsoft", query, must_have, must_have, [], condition_hint,
            min_price=40, max_price=900, exact_match_required=True
        )

    def _nintendo_profile(self, raw: str, clean: str, lower: str, condition_hint: str) -> Optional[ProductProfile]:
        m = re.search(NINTENDO_RE, lower)
        if not m:
            return None
        variant = m.group(2) or ""
        query = normalize_space(f"nintendo switch {variant}")
        must_have = ["switch"]
        if variant:
            must_have.append(variant)
        return ProductProfile(
            raw, clean, query, "console", "nintendo", query, must_have, must_have, [], condition_hint,
            min_price=60, max_price=700, exact_match_required=True
        )

    def _generic_profile(self, raw: str, clean: str, lower: str, condition_hint: str) -> ProductProfile:
        tokens = tokenize(clean)
        selected: List[str] = []

        for token in tokens:
            if token in NOISE_WORDS:
                continue
            selected.append(token)
            if len(selected) >= 8:
                break

        brand = self._find_brand(lower)
        if brand and brand not in selected:
            selected.insert(0, brand)

        query = normalize_space(" ".join(selected[:8])) or clean[:80]
        return ProductProfile(
            raw_title=raw,
            clean_title=clean,
            query=query,
            category="generic",
            brand=brand,
            canonical_model=query,
            model_tokens=selected[:5],
            must_have_tokens=selected[:2],
            excluded_tokens=[],
            condition_hint=condition_hint,
            min_price=5,
            max_price=25000,
            exact_match_required=False,
        )

    def _find_brand(self, lower: str) -> Optional[str]:
        tokens = set(tokenize(lower))
        for brand in BRANDS:
            if brand in tokens or brand in lower:
                return brand
        return None

    def _extract_storage(self, lower: str) -> str:
        m = re.search(r"\b(64|128|256|512)\s*gb\b|\b(1|2|4)\s*tb\b", lower)
        if not m:
            return ""
        if m.group(1):
            return f"{m.group(1)}gb"
        return f"{m.group(2)}tb"
