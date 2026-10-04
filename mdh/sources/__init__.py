"""Source registry. Each source exposes run(ctx, full=False) -> {table: rows_written}."""
from mdh.sources import binance_hist, bitwise, cex_reserves, near, coinmetrics, etf_issuers, defillama, fred, grayscale, nasdaq, seed_etf, sosovalue, tradingview, twentyone, xrpl_whales
from mdh.sources.misc import cboe, coingecko, deribit, feargreed
from mdh.sources.options import deribit_options
from mdh.sources.flows import coinbase_trades
from mdh.sources.cex import binance_funding, binance_spot, bybit, cftc, coinbase, hyperliquid, okx, upbit

# order matters only for readability of logs; binance is imported lazily (heavier, optional)
REGISTRY = {
    "fred": fred,
    "nasdaq": nasdaq,
    "tradingview": tradingview,
    "defillama": defillama,
    "coinmetrics": coinmetrics,
    "seed_etf": seed_etf,
    "sosovalue": sosovalue,
    "grayscale": grayscale,
    "bitwise": bitwise,
    "twentyone": twentyone,
    "etf_issuers": etf_issuers,
    "deribit": deribit,
    "deribit_options": deribit_options,
    "feargreed": feargreed,
    "coingecko": coingecko,
    "cboe": cboe,
    "coinbase": coinbase,
    "coinbase_trades": coinbase_trades,
    "binance_spot": binance_spot,
    "okx": okx,
    "bybit": bybit,
    "hyperliquid": hyperliquid,
    "cftc": cftc,
    "binance_funding": binance_funding,
    "upbit": upbit,
    "binance_hist": binance_hist,
    "xrpl_whales": xrpl_whales,
    "cex_reserves": cex_reserves,
    "near": near,
}


def get(name):
    if name == "binance":
        from mdh.sources import binance
        return binance
    return REGISTRY[name]


ALL = list(REGISTRY) + ["binance"]
