import re
from typing import List, Optional

from .models import ProductProfile
from .text import BRANDS, NOISE_WORDS, clean_text, normalize_space, strip_accents, tokenize


GPU_PATTERNS = [
    r"\b(?:nvidia\s*)?(rtx|gtx)\s*[- ]?(\d{3,4})\s*[- ]?(ti|super)?\b",
    r"\b(?:amd\s*)?(rx)\s*[- ]?(\d{3,4})\s*[- ]?(xtx|xt)?\b",
]

IPHONE_RE = r"\biphone\s*(\d{1,2}|se|xr|xs)\s*(pro max|pro|plus|max|mini)?\b"
MACBOOK_RE = r"\bmacbook\s*(air|pro)?\s*(m[1-5])?\s*(\d{2})?\s*(?:gb)?\s*(\d{3,4})?\s*(?:gb|tb)?\b"
PLAYSTATION_RE = r"\b(?:sony\s*)?(ps5|playstation\s*5|ps4|playstation\s*4)(?:\s*(slim|pro|digital|disc))?\b"
XBOX_RE = r"\b(?:xbox)\s*(series\s*[sx]|one\s*[sx]?|360)\b"
NINTENDO_RE = r"\b(?:nintendo\s*)?(switch)(?:\s*(oled|lite))?\b"


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
            canonical = " ".join(x for x in [family, number, suffix] if x).replace("  ", " ")
            query = canonical

            # Add brand only if it is useful; exact model matters more than board partner.
            brand = self._find_brand(lower)
            must_have = [family, number]
            if suffix:
                must_have.append(suffix)

            return ProductProfile(
                raw_title=raw,
                clean_title=clean,
                query=query,
                category="gpu",
                brand=brand,
                canonical_model=query,
                model_tokens=must_have,
                must_have_tokens=must_have,
                excluded_tokens=[],
                condition_hint=condition_hint,
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
        return ProductProfile(raw, clean, query, "phone", "apple", query, must_have, must_have, [], condition_hint)

    def _macbook_profile(self, raw: str, clean: str, lower: str, condition_hint: str) -> Optional[ProductProfile]:
        m = re.search(MACBOOK_RE, lower)
        if not m or "macbook" not in lower:
            return None
        kind = m.group(1) or ""
        chip = m.group(2) or ""
        ram = ""
        storage = self._extract_storage(lower)
        ram_match = re.search(r"\b(8|16|18|24|32|36|48|64)\s*gb\b", lower)
        if ram_match:
            ram = f"{ram_match.group(1)}gb"
        query = normalize_space(" ".join(x for x in ["macbook", kind, chip, ram, storage] if x))
        must_have = [t for t in ["macbook", kind, chip] if t]
        return ProductProfile(raw, clean, query, "laptop", "apple", query, must_have, must_have, [], condition_hint)

    def _playstation_profile(self, raw: str, clean: str, lower: str, condition_hint: str) -> Optional[ProductProfile]:
        m = re.search(PLAYSTATION_RE, lower)
        if not m:
            return None
        model = m.group(1).replace("playstation 5", "ps5").replace("playstation 4", "ps4").replace(" ", "")
        variant = (m.group(2) or "").strip()
        query = normalize_space(" ".join([model, variant]))
        must_have = [model]
        return ProductProfile(raw, clean, query, "console", "sony", query, must_have, must_have, [], condition_hint)

    def _xbox_profile(self, raw: str, clean: str, lower: str, condition_hint: str) -> Optional[ProductProfile]:
        m = re.search(XBOX_RE, lower)
        if not m:
            return None
        model = normalize_space(m.group(1).replace("series", "series "))
        query = f"xbox {model}"
        must_have = ["xbox"] + model.split()
        return ProductProfile(raw, clean, query, "console", "microsoft", query, must_have, must_have, [], condition_hint)

    def _nintendo_profile(self, raw: str, clean: str, lower: str, condition_hint: str) -> Optional[ProductProfile]:
        m = re.search(NINTENDO_RE, lower)
        if not m:
            return None
        variant = m.group(2) or ""
        query = normalize_space(f"nintendo switch {variant}")
        must_have = ["switch"]
        if variant:
            must_have.append(variant)
        return ProductProfile(raw, clean, query, "console", "nintendo", query, must_have, must_have, [], condition_hint)

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
            must_have_tokens=selected[:3],
            excluded_tokens=[],
            condition_hint=condition_hint,
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
