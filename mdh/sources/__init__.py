"""Source registry. Each source exposes run(ctx, full=False) -> {table: rows_written}."""
from mdh.sources import universe, treasury_dts, binance_hist, bitwise, canary, cex_reserves, near, coinmetrics, etf_issuers, defillama, fred, grayscale, nasdaq, seed_etf, sosovalue, tradingview, tv_deriv, tv_deriv_extra, twentyone, xrpl_whales
from mdh.sources.misc import cboe, cnn_feargreed, coingecko, deribit, feargreed
from mdh.sources.options import deribit_options
from mdh.sources.flows import coinbase_trades
from mdh.sources.oi5m import oi5m, oi5m_okx
from mdh.sources.hl_whales import hl_whales
from mdh.sources.cex import binance_funding, binance_spot, bybit, cftc, coinbase, hyperliquid, okx, upbit

# order matters only for readability of logs; binance is imported lazily (heavier, optional)
REGISTRY = {
    "universe": universe,      # first: finds new top-50 coins before the market sources run
    "fred": fred,
    "treasury_dts": treasury_dts,   # daily TGA (FRED WTREGEN is weekly + lags)
    "nasdaq": nasdaq,
    "tradingview": tradingview,
    "tv_deriv": tv_deriv,          # perp OI by venue + funding via TradingView (no VPN), live + history
    "tv_deriv_extra": tv_deriv_extra,  # USDC / coin-margined perps + HTX + Kraken OI so the total matches TradingView Agg (2026-11-06)
    "defillama": defillama,
    "coinmetrics": coinmetrics,
    "seed_etf": seed_etf,
    "sosovalue": sosovalue,
    "grayscale": grayscale,
    "bitwise": bitwise,
    "twentyone": twentyone,
    "canary": canary,
    "etf_issuers": etf_issuers,
    "deribit": deribit,
    "deribit_options": deribit_options,
    "feargreed": feargreed,          # alternative.me = CRYPTO fear & greed
    "cnn_feargreed": cnn_feargreed,  # CNN = US STOCKS fear & greed + 7 inputs
    "coingecko": coingecko,
    "cboe": cboe,
    "coinbase": coinbase,
    "coinbase_trades": coinbase_trades,
    "binance_spot": binance_spot,
    "okx": okx,
    "oi5m_okx": oi5m_okx,
    "oi5m": oi5m,              # VPN step (Binance + Bybit)
    "bybit": bybit,
    "hyperliquid": hyperliquid,
    "hl_whales": hl_whales,
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
