from datetime import datetime
from decimal import Decimal

from serialization_utils import to_json_safe


data = {
    "price": Decimal("68000.55"),
    "created_at": datetime.utcnow(),
    "values": [1, 2, 3],
    "nan_value": float("nan"),
}

print(to_json_safe(data))