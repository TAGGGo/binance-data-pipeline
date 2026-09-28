"""Source registry. Each source exposes run(ctx, full=False) -> {table: rows_written}."""
from mdh.sources import coinmetrics, defillama, fred, nasdaq, seed_etf, sosovalue, tradingview
from mdh.sources.misc import cboe, coingecko, deribit, feargreed

# order matters only for readability of logs; binance is imported lazily (heavier, optional)
REGISTRY = {
    "fred": fred,
    "nasdaq": nasdaq,
    "tradingview": tradingview,
    "defillama": defillama,
    "coinmetrics": coinmetrics,
    "seed_etf": seed_etf,
    "sosovalue": sosovalue,
    "deribit": deribit,
    "feargreed": feargreed,
    "coingecko": coingecko,
    "cboe": cboe,
}


def get(name):
    if name == "binance":
        from mdh.sources import binance
        return binance
    return REGISTRY[name]


ALL = list(REGISTRY) + ["binance"]
