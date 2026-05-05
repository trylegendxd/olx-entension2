from urllib.parse import quote_plus

from .base import MarketplaceFetcher, q


class OlxFetcher(MarketplaceFetcher):
    source_id = "olx_pt"
    source_name = "OLX Portugal"
    source_type = "used_active"
    reliability = 0.80

    def fetch(self, profile, listing, client_settings):
        if not self.enabled:
            return self.disabled("OLX source disabled.")
        slug = quote_plus(profile.query).replace("+", "-")
        url = f"https://www.olx.pt/items/q-{slug}/"
        return self.generic_html_fetch(url, profile, listing.get("priceValue"))


class CustoJustoFetcher(MarketplaceFetcher):
    source_id = "custojusto_pt"
    source_name = "CustoJusto"
    source_type = "used_active"
    reliability = 0.72

    def fetch(self, profile, listing, client_settings):
        if not self.enabled:
            return self.disabled("CustoJusto source disabled.")
        url = f"https://www.custojusto.pt/portugal?q={q(profile.query)}"
        return self.generic_html_fetch(url, profile, listing.get("priceValue"))


class WallapopFetcher(MarketplaceFetcher):
    source_id = "wallapop"
    source_name = "Wallapop"
    source_type = "used_active"
    reliability = 0.66

    def fetch(self, profile, listing, client_settings):
        if not self.enabled:
            return self.disabled("Wallapop source disabled.")
        url = f"https://pt.wallapop.com/search?keywords={q(profile.query)}"
        return self.generic_html_fetch(url, profile, listing.get("priceValue"))


class KuantoKustaFetcher(MarketplaceFetcher):
    source_id = "kuantokusta"
    source_name = "KuantoKusta"
    source_type = "retail_reference"
    reliability = 0.55

    def fetch(self, profile, listing, client_settings):
        if not self.enabled:
            return self.disabled("KuantoKusta source disabled.")
        url = f"https://www.kuantokusta.pt/search?q={q(profile.query)}"
        result = self.generic_html_fetch(url, profile, listing.get("priceValue"))
        for item in result.items:
            item.condition = "new"
            item.source_type = "retail_reference"
        return result


class WortenFetcher(MarketplaceFetcher):
    source_id = "worten"
    source_name = "Worten"
    source_type = "retail_reference"
    reliability = 0.52

    def fetch(self, profile, listing, client_settings):
        if not self.enabled:
            return self.disabled("Worten source disabled.")
        url = f"https://www.worten.pt/search?query={q(profile.query)}"
        result = self.generic_html_fetch(url, profile, listing.get("priceValue"))
        for item in result.items:
            item.condition = "new"
            item.source_type = "retail_reference"
        return result
