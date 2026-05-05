from dealradar.engine import DealRadarEngine
from dealradar.settings import load_settings

engine = DealRadarEngine(load_settings())

listing = {
    "title": "RTX 3080ti peças mina de água olx.pt",
    "priceValue": 360,
    "description": "Vendo gráfica RTX 3080 Ti. Testar antes de comprar.",
    "url": "https://www.olx.pt/test",
}

print(engine.debug_query(listing["title"]))
result = engine.evaluate(listing, {"minProfitPct": 25, "minimumProfitEuro": 30, "feePct": 8})
print(result["verdict"], result["summary"])
for source in result["sources"]:
    print(source["id"], source["status"], source["sampleSize"], source.get("median"), source.get("error"))
