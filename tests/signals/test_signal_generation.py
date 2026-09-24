"""Tests for app/signals/signal_generation.py::strategy_factory.

These pin the *inert* feature flags in `Config`. Several flags read as enabled
but do nothing unless a companion value is also set (n-tick confirmation needs
`N_TICK_CONFIRMATION > 1`; the ATR gate needs both period and multiplier > 0).
A flag that looks on but isn't is worse than one that's plainly off, so these
tests assert what the live wiring actually produces under the shipped config.
"""

from app.config.settings import Config
from app.signals.signal_generation import strategy_factory
from app.signals.strategies.ntick_confirmed_signal_strategy import (
    NTickConfirmedSignalStrategy,
)


def _chain(strategy):
    """Every wrapper in the built strategy, outermost first.

    `strategy_factory` composes several optional wrappers (session filter,
    n-tick confirmation, MTF, ATR), each holding its inner strategy under a
    different attribute name -- so an `isinstance` check on the outermost
    object alone proves nothing about what's underneath it.
    """
    seen = []
    node = strategy
    while node is not None and node not in seen:
        seen.append(node)
        node = next(
            (
                inner
                for attr in ("strategy", "base", "base_strategy", "entry_strategy")
                for inner in [getattr(node, attr, None)]
                if inner is not None and not isinstance(inner, (str, int, float))
            ),
            None,
        )
    return seen


def test_shipped_config_does_not_wrap_in_ntick_confirmation():
    """The live bot has no tick confirmation today. If someone flips
    USE_N_TICK_CONFIRMATION back on without also raising N_TICK_CONFIRMATION,
    this stays green and the flag stays a lie -- so the companion assertion
    below pins the reason, not just the outcome."""
    strategy = strategy_factory(config=Config, symbol="EURUSD")

    assert not any(
        isinstance(s, NTickConfirmedSignalStrategy) for s in _chain(strategy)
    )
    assert int(getattr(Config, "N_TICK_CONFIRMATION", 0) or 0) <= 1, (
        "N_TICK_CONFIRMATION is now > 1, so the wrapper would engage -- "
        "this test and the comment in settings.py both need revisiting"
    )


def test_ntick_wrapper_does_engage_once_the_count_is_raised():
    """Proves the assertion above is about configuration, not a broken factory:
    the same call wraps as soon as n_ticks > 1."""
    strategy = strategy_factory(
        config=Config, symbol="EURUSD", use_n_tick=True, n_ticks=3
    )

    wrappers = [
        s for s in _chain(strategy) if isinstance(s, NTickConfirmedSignalStrategy)
    ]
    assert wrappers, [type(s).__name__ for s in _chain(strategy)]
    assert wrappers[0].n_ticks == 3
