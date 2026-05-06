from dealradar.engine import DealRadarEngine
from dealradar.settings import load_settings
from dealradar.models import ComparableItem, SourceResult

engine = DealRadarEngine(load_settings())
profile = engine.query_builder.build("RTX 3060ti peças mina de água olx.pt", "")

assert profile.query == "rtx 3060 ti", profile.query
assert profile.max_price <= 500, profile.max_price

bad_item = ComparableItem(
    source_id="test",
    source_name="Test",
    title="RTX 3060 Ti",
    price=18500,
    source_type="used_active",
    reliability=1,
    similarity=1,
)

good_item = ComparableItem(
    source_id="test",
    source_name="Test",
    title="RTX 3060 Ti",
    price=300,
    source_type="used_active",
    reliability=1,
    similarity=1,
)

source = SourceResult("test", "Test", "used_active", 1, "ok", items=[bad_item, good_item])
items = engine._collect_comparables([source], profile, 300)
assert len(items) == 1, items
assert items[0].price == 300, items[0].price

market = engine._calculate_market(items)
assert market["usedMedian"] is None, market
assert not market["trusted"], market

print("sanity ok")
