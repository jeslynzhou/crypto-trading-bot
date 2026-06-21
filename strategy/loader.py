import importlib
import importlib.util
import os
import logging

from strategy.macd import MACDStrategy
from strategy.rsi import RSIStrategy
from strategy.mean_reversion import MeanReversionStrategy
from strategy.supertrend import SupertrendStrategy
from strategy.base import BaseStrategy

logger = logging.getLogger(__name__)

STRATEGY_CLASSES = {
    "MACD": MACDStrategy,
    "RSI": RSIStrategy,
    "BollingerBands": MeanReversionStrategy,
    "Supertrend": SupertrendStrategy,
}

CUSTOM_DIR = os.path.join(os.path.dirname(__file__), "custom")


def discover_custom_strategies() -> dict[str, type]:
    found = {}
    if not os.path.isdir(CUSTOM_DIR):
        return found
    for filename in os.listdir(CUSTOM_DIR):
        if filename.startswith("_") or not filename.endswith(".py"):
            continue
        filepath = os.path.join(CUSTOM_DIR, filename)
        module_name = f"strategy.custom.{filename[:-3]}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (isinstance(attr, type)
                        and issubclass(attr, BaseStrategy)
                        and attr is not BaseStrategy
                        and attr_name.endswith("Strategy")):
                    name = attr_name.replace("Strategy", "")
                    found[name] = attr
                    logger.info("Discovered custom strategy: %s from %s", name, filename)
        except Exception as e:
            logger.error("Failed to load custom strategy %s: %s", filename, e)
    return found


def get_all_strategy_classes() -> dict[str, type]:
    all_classes = dict(STRATEGY_CLASSES)
    all_classes.update(discover_custom_strategies())
    return all_classes


def get_all_strategy_names() -> list[str]:
    return list(get_all_strategy_classes().keys())


def build_strategy(name: str, params: dict = None):
    all_classes = get_all_strategy_classes()
    cls = all_classes.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy: {name}")
    if params:
        return cls(**params)
    return cls()


def build_all_strategies(custom_params: dict = None):
    all_classes = get_all_strategy_classes()
    strategies = []
    custom_params = custom_params or {}
    for name, cls in all_classes.items():
        params = custom_params.get(name, {})
        strategies.append(cls(**params) if params else cls())
    return strategies
