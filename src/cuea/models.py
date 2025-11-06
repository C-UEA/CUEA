# src/cuea/models.py
from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Optional, Dict

class Ticker(BaseModel):
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    timestamp: Optional[int]

class OrderRequest(BaseModel):
    symbol: str
    side: str  # buy/sell
    qty: Decimal
    type: str  # market/limit
    price: Optional[Decimal] = None
    params: dict = Field(default_factory=dict)

class Order(BaseModel):
    id: str
    symbol: str
    side: str
    price: Optional[Decimal]
    qty: Decimal
    filled: Decimal
    status: str

class Balance(BaseModel):
    asset: str
    free: Decimal
    locked: Decimal


class Trade(BaseModel):
    id: str
    symbol: str
    price: Decimal
    qty: Decimal
    side: str
    timestamp: Optional[int] = None
    raw: Optional[dict] = None


class Position(BaseModel):
    symbol: str
    size: Decimal
    entry_price: Optional[Decimal]
    unrealized_pnl: Decimal
    leverage: Optional[Decimal] = None
    margin: Optional[dict] = None
    liquid_price: Optional[Decimal] = None
    raw: Optional[dict] = None


class FuturesOrder(Order):
    pass


# --- Options models ---

class OptionContract(BaseModel):
    symbol: str
    underlying: str
    strike: Decimal
    expiry: Optional[int]
    right: str
    option_type: Optional[str] = None
    raw: Optional[Dict] = None


class OptionsOrder(Order):
    pass


class OptionsChain(BaseModel):
    underlying: str
    contracts: list[OptionContract]
    raw: Optional[Dict] = None


# --- Margin models ---

class MarginBalance(BaseModel):
    asset: str
    total: Decimal
    available: Decimal
    borrowed: Optional[Decimal] = None
    raw: Optional[Dict] = None


class MarginPosition(BaseModel):
    symbol: str
    size: Decimal
    entry_price: Optional[Decimal]
    unrealized_pnl: Decimal
    margin_used: Decimal
    liquidation_price: Optional[Decimal] = None
    raw: Optional[Dict] = None
