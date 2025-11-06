
from decimal import Decimal
def to_decimal(x):
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal(0)
