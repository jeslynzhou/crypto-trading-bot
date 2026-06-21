from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class Signal(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class TradeSignal:
    signal: Signal
    confidence: float
    reason: str
    price: float = 0.0

    def __repr__(self):
        return f"TradeSignal({self.signal.value}, conf={self.confidence:.2f}, reason={self.reason})"


class BaseStrategy(ABC):
    def __init__(self, name: str):
        self.name = name
        self._candles: list[dict] = []

    def on_candle(self, candle: dict):
        self._candles.append(candle)
        max_history = getattr(self, "max_history", 200)
        if len(self._candles) > max_history:
            self._candles = self._candles[-max_history:]

    @abstractmethod
    def generate_signal(self) -> TradeSignal:
        pass

    @property
    def closes(self) -> list[float]:
        return [c["close"] for c in self._candles]

    @property
    def volumes(self) -> list[float]:
        return [c["volume"] for c in self._candles]

    @property
    def highs(self) -> list[float]:
        return [c["high"] for c in self._candles]

    @property
    def lows(self) -> list[float]:
        return [c["low"] for c in self._candles]

    def reset(self):
        self._candles = []
