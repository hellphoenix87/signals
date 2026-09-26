"""Per-symbol configs (docs/plans/in-progress/per-symbol-config.md).

`Config` is the EURUSD config and the template for every other pair. A pair that needs different
settings gets a subclass here: it inherits every EURUSD setting and overrides only what differs, each
override with the reason for it. `config_for(symbol)` is the single lookup the live code and the
backtest use; a symbol without its own class gets `Config` itself.
"""

from app.config.settings import Config


class USDJPYConfig(Config):
    """USDJPY: the EURUSD template with these changes."""

    # The per-pair session study (docs/test-results/session-filter-per-pair-analysis.md) found USDJPY's
    # 08-18 UTC block reproducibly BETTER -- the opposite of EURUSD. So trade 08-18 and block 19-07.
    SESSION_FILTER_BLOCKED_HOURS_UTC_BY_SYMBOL = {
        **Config.SESSION_FILTER_BLOCKED_HOURS_UTC_BY_SYMBOL,
        "USDJPY": list(range(19, 24)) + list(range(0, 8)),
    }

    # Money exits ($1 cap, $2 staircase, $5 soft SL) stay as in the template: live lot sizing
    # (1% of the sizing balance over a 5-pip stop) gives USDJPY ~0.3 lot, where a pip is worth ~$2 --
    # the same as EURUSD at 0.2 lot -- so the same dollars mean the same pip distances.


SYMBOL_CONFIGS: dict[str, type[Config]] = {
    "USDJPY": USDJPYConfig,
}


def config_for(symbol: str | None) -> type[Config]:
    """Return the config class for `symbol` (EURUSD / unknown symbols -> `Config`)."""
    return SYMBOL_CONFIGS.get(str(symbol or ""), Config)
