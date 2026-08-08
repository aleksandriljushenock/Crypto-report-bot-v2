from datetime import datetime, timezone
from decimal import Decimal

from serialization_utils import to_json_safe


def test_to_json_safe_handles_common_runtime_types():
    data = {
        "price": Decimal("68000.55"),
        "created_at": datetime.now(timezone.utc),
        "values": [1, 2, 3],
        "nan_value": float("nan"),
    }
    safe = to_json_safe(data)
    assert safe["price"] == 68000.55
    assert safe["values"] == [1, 2, 3]
    assert safe["nan_value"] is None
