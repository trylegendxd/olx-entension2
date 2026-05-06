import html
import re
import unicodedata
from typing import Iterable, List, Optional, Set

BAD_WORDS = [
    "avariado", "avariada", "danificado", "danificada", "defeito", "defeituoso",
    "defeituosa", "não funciona", "nao funciona", "partido", "partida", "reparar",
    "reparação", "reparacao", "peças", "pecas", "para peças", "para pecas",
    "crash", "crasha", "sem garantia", "bloqueado", "bloqueada", "icloud",
    "conta bloqueada", "mining", "mineracao", "mineração", "artefactos", "artefatos"
]

LOCATION_WORDS = {
    "portugal", "braga", "porto", "lisboa", "aveiro", "coimbra", "faro", "setubal",
    "setúbal", "viana", "castelo", "vila", "real", "viseu", "guarda", "evora",
    "évora", "beja", "santarem", "santarém", "leiria", "braganca", "bragança",
    "barcelos", "guimaraes", "guimarães", "famalicao", "famalicão", "mina", "agua",
    "água", "sande", "amares", "prado"
}

NOISE_WORDS = {
    "olx", "olxpt", "www", "pt", "html", "anuncio", "anúncio", "comprar", "vender",
    "vendo", "vende", "troco", "troca", "urgente", "negociavel", "negociável",
    "preco", "preço", "barato", "barata", "novo", "nova", "usado", "usada",
    "excelente", "estado", "como", "novo", "caixa", "original", "portes", "envio",
    "entrega", "maos", "mãos", "selado", "selada", "garantia"
} | LOCATION_WORDS

BRANDS = {
    "apple", "samsung", "xiaomi", "huawei", "sony", "jbl", "bose", "nintendo",
    "playstation", "ps5", "ps4", "xbox", "microsoft", "asus", "msi", "gigabyte",
    "evga", "zotac", "inno3d", "palit", "sapphire", "powercolor", "nvidia", "amd",
    "intel", "lenovo", "hp", "dell", "acer", "canon", "nikon", "gopro", "dyson"
}


def strip_accents(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\u00a0", " ")
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"\bolx\.pt\b|\bwww\.olx\.pt\b|\bolx portugal\b", " ", value, flags=re.I)
    value = re.sub(r"\b\d{1,2}:\d{2}\b", " ", value)
    value = re.sub(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", " ", value)
    value = re.sub(r"\b\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?\s*€", " ", value)
    value = re.sub(r"[^\w\s+\-.]", " ", value, flags=re.UNICODE)
    return normalize_space(value)


def tokenize(value: str) -> List[str]:
    value = strip_accents(clean_text(value)).lower()
    raw = re.split(r"\s+", value)
    tokens = []
    for token in raw:
        token = token.strip(" .-+_")
        if not token or len(token) <= 1:
            continue
        if token in NOISE_WORDS:
            continue
        tokens.append(token)
    return tokens


def detect_bad_words(text: str) -> List[str]:
    lower = (text or "").lower()
    lower_ascii = strip_accents(lower)
    found = []
    for word in BAD_WORDS:
        word_ascii = strip_accents(word.lower())
        if re.search(rf"\b{re.escape(word_ascii)}\b", lower_ascii):
            found.append(word)
    return sorted(set(found))


def token_set(value: str) -> Set[str]:
    return set(tokenize(value))


def simple_similarity(a: str, b: str, must_have: Optional[Iterable[str]] = None) -> float:
    a_tokens = token_set(a)
    b_tokens = token_set(b)

    if not a_tokens or not b_tokens:
        return 0.0

    intersection = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens)
    jaccard = intersection / union if union else 0.0

    containment = intersection / max(1, min(len(a_tokens), len(b_tokens)))
    score = 0.65 * containment + 0.35 * jaccard

    for token in must_have or []:
        if token and token in b_tokens:
            score += 0.08
        elif token:
            score -= 0.18

    return max(0.0, min(1.0, score))


def best_title_from_context(context: str, fallback: str) -> str:
    context = normalize_space(context)
    context = re.sub(r"\b\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?\s*€.*$", "", context)
    context = re.sub(r"\s+", " ", context)
    if 8 <= len(context) <= 140:
        return context
    return fallback


def normalized_for_match(value: str) -> str:
    value = strip_accents(clean_text(value)).lower()
    value = value.replace("-", " ")
    value = re.sub(r"\s+", " ", value)
    return f" {value.strip()} "


def contains_token_or_phrase(text: str, token: str) -> bool:
    if not token:
        return True
    haystack = normalized_for_match(text)
    needle = normalized_for_match(token).strip()
    if " " in needle:
        return f" {needle} " in haystack
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def exact_model_match(title: str, must_have_tokens) -> bool:
    if not must_have_tokens:
        return True
    return all(contains_token_or_phrase(title, token) for token in must_have_tokens if token)


def excluded_model_match(title: str, excluded_tokens) -> bool:
    if not excluded_tokens:
        return False
    return any(contains_token_or_phrase(title, token) for token in excluded_tokens if token)
