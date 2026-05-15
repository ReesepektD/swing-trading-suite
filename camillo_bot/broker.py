"""
Broker abstraction layer.

AlpacaBroker  — connects to Alpaca paper or live via alpaca-py
DryRunBroker  — logs orders without executing, for local testing
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared data types
# ---------------------------------------------------------------------------

@dataclass
class AccountInfo:
    equity:          float
    cash:            float
    buying_power:    float
    portfolio_value: float


@dataclass
class BrokerPosition:
    ticker:           str
    qty:              float
    market_value:     float
    avg_cost:         float
    unrealized_pl:    float
    unrealized_plpc:  float  # fraction, e.g. 0.15 = +15%


@dataclass
class Order:
    id:               str
    ticker:           str
    side:             str    # "buy" | "sell"
    notional:         Optional[float]
    qty:              Optional[float]
    filled_avg_price: Optional[float]
    status:           str


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class BaseBroker(ABC):
    @abstractmethod
    def get_account(self) -> AccountInfo: ...

    @abstractmethod
    def get_positions(self) -> list: ...          # list[BrokerPosition]

    @abstractmethod
    def get_price(self, ticker: str) -> float: ...

    @abstractmethod
    def place_buy(self, ticker: str, notional: float) -> Order: ...

    @abstractmethod
    def place_sell(self, ticker: str, qty: float) -> Order: ...

    @abstractmethod
    def place_sell_notional(self, ticker: str, notional: float) -> Order: ...

    @abstractmethod
    def is_market_open(self) -> bool: ...

    @abstractmethod
    def get_position(self, ticker: str) -> Optional[BrokerPosition]: ...


# ---------------------------------------------------------------------------
# Alpaca implementation
# ---------------------------------------------------------------------------

class AlpacaBroker(BaseBroker):
    """
    Wraps alpaca-py TradingClient.
    Uses notional (dollar-based) orders so fractional shares work automatically.
    """

    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient

        self._trading = TradingClient(api_key, secret_key, paper=paper)
        self._data    = StockHistoricalDataClient(api_key, secret_key)
        mode = "PAPER" if paper else "LIVE"
        log.info("AlpacaBroker connected [%s]", mode)

    def get_account(self) -> AccountInfo:
        a = self._trading.get_account()
        return AccountInfo(
            equity          = float(a.equity),
            cash            = float(a.cash),
            buying_power    = float(a.buying_power),
            portfolio_value = float(a.portfolio_value),
        )

    def get_positions(self) -> list:
        return [
            BrokerPosition(
                ticker          = p.symbol,
                qty             = float(p.qty),
                market_value    = float(p.market_value),
                avg_cost        = float(p.avg_entry_price),
                unrealized_pl   = float(p.unrealized_pl),
                unrealized_plpc = float(p.unrealized_plpc),
            )
            for p in self._trading.get_all_positions()
        ]

    def get_position(self, ticker: str) -> Optional[BrokerPosition]:
        try:
            p = self._trading.get_open_position(ticker)
            return BrokerPosition(
                ticker          = p.symbol,
                qty             = float(p.qty),
                market_value    = float(p.market_value),
                avg_cost        = float(p.avg_entry_price),
                unrealized_pl   = float(p.unrealized_pl),
                unrealized_plpc = float(p.unrealized_plpc),
            )
        except Exception:
            return None

    def get_price(self, ticker: str) -> float:
        from alpaca.data.requests import StockLatestQuoteRequest
        req   = StockLatestQuoteRequest(symbol_or_symbols=ticker)
        quote = self._data.get_stock_latest_quote(req)
        # Use mid-price (ask+bid)/2 for cleaner fills on limit orders
        q = quote[ticker]
        return round((float(q.ask_price) + float(q.bid_price)) / 2, 4)

    def place_buy(self, ticker: str, notional: float) -> Order:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        req = MarketOrderRequest(
            symbol        = ticker,
            notional      = round(notional, 2),
            side          = OrderSide.BUY,
            time_in_force = TimeInForce.DAY,
        )
        o = self._trading.submit_order(req)
        log.info("BUY  %s $%.2f → order %s [%s]", ticker, notional, o.id, o.status)
        return Order(
            id               = str(o.id),
            ticker           = ticker,
            side             = "buy",
            notional         = notional,
            qty              = None,
            filled_avg_price = float(o.filled_avg_price) if o.filled_avg_price else None,
            status           = str(o.status),
        )

    def place_sell(self, ticker: str, qty: float) -> Order:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        req = MarketOrderRequest(
            symbol        = ticker,
            qty           = qty,
            side          = OrderSide.SELL,
            time_in_force = TimeInForce.DAY,
        )
        o = self._trading.submit_order(req)
        log.info("SELL %s %.4f shares → order %s [%s]", ticker, qty, o.id, o.status)
        return Order(
            id               = str(o.id),
            ticker           = ticker,
            side             = "sell",
            notional         = None,
            qty              = qty,
            filled_avg_price = float(o.filled_avg_price) if o.filled_avg_price else None,
            status           = str(o.status),
        )

    def place_sell_notional(self, ticker: str, notional: float) -> Order:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        req = MarketOrderRequest(
            symbol        = ticker,
            notional      = round(notional, 2),
            side          = OrderSide.SELL,
            time_in_force = TimeInForce.DAY,
        )
        o = self._trading.submit_order(req)
        log.info("SELL %s $%.2f → order %s [%s]", ticker, notional, o.id, o.status)
        return Order(
            id               = str(o.id),
            ticker           = ticker,
            side             = "sell",
            notional         = notional,
            qty              = None,
            filled_avg_price = float(o.filled_avg_price) if o.filled_avg_price else None,
            status           = str(o.status),
        )

    def is_market_open(self) -> bool:
        return self._trading.get_clock().is_open


# ---------------------------------------------------------------------------
# Dry-run broker (no real orders — safe for local dev and CI)
# ---------------------------------------------------------------------------

class DryRunBroker(BaseBroker):
    """
    Simulates orders with an in-memory portfolio.
    Starts with $100,000 virtual cash.
    """

    def __init__(self, starting_cash: float = 100_000.0):
        self._cash      = starting_cash
        self._positions: dict = {}   # ticker → BrokerPosition
        self._order_seq = 0
        log.info("DryRunBroker initialized with $%.2f virtual cash", starting_cash)

    def get_account(self) -> AccountInfo:
        mkt_val = sum(p.market_value for p in self._positions.values())
        equity  = self._cash + mkt_val
        return AccountInfo(
            equity          = equity,
            cash            = self._cash,
            buying_power    = self._cash,
            portfolio_value = equity,
        )

    def get_positions(self) -> list:
        return list(self._positions.values())

    def get_position(self, ticker: str) -> Optional[BrokerPosition]:
        return self._positions.get(ticker)

    def get_price(self, ticker: str) -> float:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="1d")
        if hist.empty:
            raise ValueError(f"No price data for {ticker}")
        return float(hist["Close"].iloc[-1])

    def place_buy(self, ticker: str, notional: float) -> Order:
        price  = self.get_price(ticker)
        shares = notional / price
        self._order_seq += 1

        if ticker in self._positions:
            pos = self._positions[ticker]
            total_cost  = pos.avg_cost * pos.qty + price * shares
            total_shares = pos.qty + shares
            self._positions[ticker] = BrokerPosition(
                ticker          = ticker,
                qty             = total_shares,
                market_value    = total_shares * price,
                avg_cost        = total_cost / total_shares,
                unrealized_pl   = 0.0,
                unrealized_plpc = 0.0,
            )
        else:
            self._positions[ticker] = BrokerPosition(
                ticker          = ticker,
                qty             = shares,
                market_value    = notional,
                avg_cost        = price,
                unrealized_pl   = 0.0,
                unrealized_plpc = 0.0,
            )
        self._cash -= notional
        log.info("[DRY] BUY  %s $%.2f @ $%.2f = %.4f shares", ticker, notional, price, shares)
        return Order(
            id=str(self._order_seq), ticker=ticker, side="buy",
            notional=notional, qty=shares, filled_avg_price=price, status="filled"
        )

    def place_sell(self, ticker: str, qty: float) -> Order:
        price = self.get_price(ticker)
        proceeds = qty * price
        self._order_seq += 1

        if ticker in self._positions:
            pos = self._positions[ticker]
            remaining = pos.qty - qty
            if remaining <= 0.001:
                del self._positions[ticker]
            else:
                self._positions[ticker] = BrokerPosition(
                    ticker=ticker, qty=remaining, market_value=remaining * price,
                    avg_cost=pos.avg_cost,
                    unrealized_pl=(price - pos.avg_cost) * remaining,
                    unrealized_plpc=(price / pos.avg_cost - 1),
                )
        self._cash += proceeds
        log.info("[DRY] SELL %s %.4f shares @ $%.2f = $%.2f", ticker, qty, price, proceeds)
        return Order(
            id=str(self._order_seq), ticker=ticker, side="sell",
            notional=proceeds, qty=qty, filled_avg_price=price, status="filled"
        )

    def place_sell_notional(self, ticker: str, notional: float) -> Order:
        price = self.get_price(ticker)
        qty   = notional / price
        return self.place_sell(ticker, qty)

    def is_market_open(self) -> bool:
        from datetime import datetime, timezone
        import zoneinfo
        et  = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(et)
        if now.weekday() >= 5:
            return False
        open_t  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
        close_t = now.replace(hour=16, minute=0,  second=0, microsecond=0)
        return open_t <= now <= close_t
