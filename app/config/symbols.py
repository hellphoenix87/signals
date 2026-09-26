"""Per-symbol configs (docs/plans/in-progress/per-symbol-config.md).

`Config` is the EURUSD config and the template for every other pair. A pair that needs different
settings gets a subclass here: it inherits every EURUSD setting and overrides only what differs, each
override with the reason for it. `config_for(symbol)` is the single lookup the live code and the
backtest use; a symbol without its own class gets `Config` itself.
"""

from app.config.settings import Config


class USDJPYConfig(Config):
    """USDJPY: a copy of the EURUSD template."""


SYMBOL_CONFIGS: dict[str, type[Config]] = {
    "USDJPY": USDJPYConfig,
}


def config_for(symbol: str | None) -> type[Config]:
    """Return the config class for `symbol` (EURUSD / unknown symbols -> `Config`)."""
    return SYMBOL_CONFIGS.get(str(symbol or ""), Config)
