import importlib
import importlib.util
import os
import logging
import tempfile
from typing import Optional

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


def _load_strategy_from_code(code: str, source_name: str, found: dict):
    try:
        module_name = f"strategy.custom.db_{source_name}"
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        tmp.write(code)
        tmp.close()
        spec = importlib.util.spec_from_file_location(module_name, tmp.name)
        if spec is None or spec.loader is None:
            os.unlink(tmp.name)
            return
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        os.unlink(tmp.name)
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (isinstance(attr, type)
                    and issubclass(attr, BaseStrategy)
                    and attr is not BaseStrategy
                    and attr_name.endswith("Strategy")):
                name = attr_name.replace("Strategy", "")
                found[name] = attr
                logger.info("Loaded strategy from DB: %s", name)
    except Exception as e:
        logger.error("Failed to load strategy %s from DB: %s", source_name, e)


def _scan_dir(directory: str, found: dict):
    if not os.path.isdir(directory):
        return
    for filename in os.listdir(directory):
        if filename.startswith("_") or not filename.endswith(".py"):
            continue
        filepath = os.path.join(directory, filename)
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


def discover_custom_strategies(user_id: Optional[str] = None) -> dict[str, type]:
    found = {}
    _scan_dir(CUSTOM_DIR, found)
    if user_id:
        try:
            from data.storage import get_user_strategies
            for strat in get_user_strategies(user_id):
                _load_strategy_from_code(strat["code"], strat["name"], found)
        except Exception as e:
            logger.error("Failed to load DB strategies for %s: %s", user_id, e)
    return found


def get_all_strategy_classes(user_id: Optional[str] = None) -> dict[str, type]:
    all_classes = dict(STRATEGY_CLASSES)
    all_classes.update(discover_custom_strategies(user_id))
    return all_classes


def get_all_strategy_names(user_id: Optional[str] = None) -> list[str]:
    return list(get_all_strategy_classes(user_id).keys())


def build_strategy(name: str, params: dict = None, user_id: Optional[str] = None,
                   user_dir: Optional[str] = None):
    all_classes = get_all_strategy_classes(user_id)
    if user_dir:
        _scan_dir(user_dir, all_classes)
    cls = all_classes.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy: {name}")
    if params:
        return cls(**params)
    return cls()


def build_all_strategies(custom_params: dict = None, user_id: Optional[str] = None):
    all_classes = get_all_strategy_classes(user_id)
    strategies = []
    custom_params = custom_params or {}
    for name, cls in all_classes.items():
        params = custom_params.get(name, {})
        strategies.append(cls(**params) if params else cls())
    return strategies
