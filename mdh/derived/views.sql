-- Derived views: rebuilt after every update (cheap; they are views, not tables).
-- Each statement is separated by a line containing only ';;' so the loader can skip views
-- whose base tables don't exist yet.

-- ETF flows: one row per (date, asset). SoSoValue preferred, Farside fills older BTC/ETH history.
CREATE OR REPLACE VIEW v_etf_flows AS
WITH ranked AS (
    SELECT *, row_number() OVER (PARTITION BY date, asset
                                 ORDER BY CASE source WHEN 'sosovalue' THEN 1 ELSE 2 END) AS rn
    FROM etf_flows_daily
)
SELECT date, asset, net_inflow_usd, value_traded_usd, net_assets_usd, source,
       sum(net_inflow_usd) OVER (PARTITION BY asset ORDER BY date) AS cum_net_inflow_usd
FROM ranked WHERE rn = 1
ORDER BY date, asset
;;

-- Crypto market structure (daily): TradingView CRYPTOCAP + DefiLlama stablecoin supply.
-- btc_dom_ex_stables = BTC mcap / (TOTAL - all stablecoins)            [DefiLlama stables]
-- btc_dom_ex_usdt_usdc = BTC.D / (100 - USDT.D - USDC.D)               [TradingView-only, consistent basis]
CREATE OR REPLACE VIEW v_crypto_market_daily AS
WITH tv AS (
    SELECT CAST(ts AS DATE) AS date,
        max(close) FILTER (WHERE symbol = 'CRYPTOCAP:TOTAL')    AS total_mcap,
        max(close) FILTER (WHERE symbol = 'CRYPTOCAP:TOTAL2')   AS total2_mcap,
        max(close) FILTER (WHERE symbol = 'CRYPTOCAP:TOTAL3')   AS total3_mcap,
        max(close) FILTER (WHERE symbol = 'CRYPTOCAP:BTC.D')    AS btc_d,
        max(close) FILTER (WHERE symbol = 'CRYPTOCAP:ETH.D')    AS eth_d,
        max(close) FILTER (WHERE symbol = 'CRYPTOCAP:USDT.D')   AS usdt_d,
        max(close) FILTER (WHERE symbol = 'CRYPTOCAP:USDC.D')   AS usdc_d,
        max(close) FILTER (WHERE symbol = 'CRYPTOCAP:OTHERS.D') AS others_d
    FROM tv_bars WHERE interval = '1d' GROUP BY 1
),
st AS (SELECT date, circulating_usd AS stablecoin_mcap FROM stablecoin_supply WHERE stablecoin = 'ALL')
SELECT tv.*,
       total_mcap * btc_d / 100                                         AS btc_mcap,
       total_mcap * eth_d / 100                                         AS eth_mcap,
       st.stablecoin_mcap,
       100 * (total_mcap * btc_d / 100) / (total_mcap - st.stablecoin_mcap) AS btc_dom_ex_stables,
       100 * btc_d / (100 - usdt_d - coalesce(usdc_d, 0))               AS btc_dom_ex_usdt_usdc,
       total3_mcap - total_mcap * (coalesce(usdt_d, 0) + coalesce(usdc_d, 0)) / 100 AS total3_ex_usdt_usdc
FROM tv LEFT JOIN st USING (date)
ORDER BY date
;;

-- Fed net liquidity ($ trillions) = Fed balance sheet - TGA - reverse repo, daily (as-of joins).
CREATE OR REPLACE VIEW v_net_liquidity AS
WITH r AS (SELECT date, value * 1000 AS rrp FROM fred_series WHERE series_id = 'RRPONTSYD'),
     w AS (SELECT date, value AS walcl FROM fred_series WHERE series_id = 'WALCL'),
     t AS (SELECT date, value AS tga FROM fred_series WHERE series_id = 'WTREGEN'),
     rw AS (SELECT r.date, r.rrp, w.walcl FROM r ASOF LEFT JOIN w ON r.date >= w.date)
SELECT rw.date, rw.walcl / 1e6 AS fed_assets_tn, t.tga / 1e6 AS tga_tn, rw.rrp / 1e6 AS rrp_tn,
       (rw.walcl - t.tga - rw.rrp) / 1e6 AS net_liquidity_tn
FROM rw ASOF LEFT JOIN t ON rw.date >= t.date
ORDER BY rw.date
;;

-- One wide daily row for cross-asset analysis (NULL where a market was closed).
CREATE OR REPLACE VIEW v_macro_daily AS
WITH cal AS (SELECT CAST(d AS DATE) AS date FROM range(DATE '2014-01-01', current_date + 1, INTERVAL 1 DAY) t(d)),
fred AS (
    SELECT date,
        max(value) FILTER (WHERE series_id = 'DGS10')    AS us10y,
        max(value) FILTER (WHERE series_id = 'DGS2')     AS us2y,
        max(value) FILTER (WHERE series_id = 'DFII10')   AS us10y_real,
        max(value) FILTER (WHERE series_id = 'DTWEXBGS') AS usd_broad,
        max(value) FILTER (WHERE series_id = 'SP500')    AS spx,
        max(value) FILTER (WHERE series_id = 'NASDAQCOM') AS nasdaq,
        max(value) FILTER (WHERE series_id = 'VIXCLS')   AS vix,
        max(value) FILTER (WHERE series_id = 'BAMLH0A0HYM2') AS hy_spread
    FROM fred_series GROUP BY 1
),
px AS (
    SELECT date,
        max(value) FILTER (WHERE asset = 'BTC' AND metric = 'PriceUSD') AS btc_usd,
        max(value) FILTER (WHERE asset = 'ETH' AND metric = 'PriceUSD') AS eth_usd,
        max(value) FILTER (WHERE asset = 'BTC' AND metric = 'CapMVRVCur') AS btc_mvrv
    FROM onchain_daily GROUP BY 1
),
dxy AS (SELECT CAST(ts AS DATE) AS date, close AS dxy FROM tv_bars WHERE symbol = 'TVC:DXY' AND interval = '1d'),
etf AS (
    SELECT date,
        sum(net_inflow_usd) FILTER (WHERE asset = 'BTC') AS btc_etf_flow_usd,
        sum(net_inflow_usd) FILTER (WHERE asset = 'ETH') AS eth_etf_flow_usd,
        sum(net_inflow_usd) FILTER (WHERE asset = 'SOL') AS sol_etf_flow_usd,
        sum(net_inflow_usd) FILTER (WHERE asset = 'XRP') AS xrp_etf_flow_usd
    FROM v_etf_flows GROUP BY 1
)
SELECT cal.date, px.btc_usd, px.eth_usd, px.btc_mvrv,
       m.total_mcap, m.total3_mcap, m.btc_d, m.btc_dom_ex_stables, m.stablecoin_mcap,
       fred.us10y, fred.us2y, fred.us10y_real, dxy.dxy, fred.usd_broad,
       fred.spx, fred.nasdaq, coalesce(vx.close, fred.vix) AS vix, fred.hy_spread,
       nl.net_liquidity_tn, fg.value AS fear_greed,
       etf.btc_etf_flow_usd, etf.eth_etf_flow_usd, etf.sol_etf_flow_usd, etf.xrp_etf_flow_usd
FROM cal
LEFT JOIN px USING (date)
LEFT JOIN v_crypto_market_daily m USING (date)
LEFT JOIN fred USING (date)
LEFT JOIN dxy USING (date)
LEFT JOIN v_net_liquidity nl USING (date)
LEFT JOIN fear_greed fg USING (date)
LEFT JOIN etf USING (date)
LEFT JOIN vix_daily vx USING (date)
WHERE cal.date <= current_date
ORDER BY cal.date
;;

-- Coinbase premium: Coinbase BTC-USD vs Binance BTC-USDT converted to USD with Coinbase USDT-USD.
-- Positive = US buyers paying up. Hourly (from binance_1h) and daily (from Binance daily klines).
CREATE OR REPLACE VIEW v_coinbase_premium_1h AS
WITH cb AS (SELECT symbol, ts, close FROM cex_spot WHERE venue = 'coinbase' AND interval = '1h' AND symbol <> 'USDT'),
     u  AS (SELECT ts, close AS usdt_usd FROM cex_spot WHERE venue = 'coinbase' AND interval = '1h' AND symbol = 'USDT'),
     bn AS (SELECT replace(symbol, 'USDT', '') AS symbol, "timestamp" AS ts, spot_close FROM binance_1h)
SELECT cb.symbol, cb.ts, cb.close AS coinbase_usd, bn.spot_close AS binance_usdt, u.usdt_usd,
       (cb.close / (bn.spot_close * coalesce(u.usdt_usd, 1)) - 1) * 1e4 AS premium_bp
FROM cb JOIN bn USING (symbol, ts) LEFT JOIN u USING (ts)
-- completed hours only: the current candle's close depends on when each exchange was polled
WHERE cb.ts < date_trunc('hour', now()::TIMESTAMP) - INTERVAL 1 HOUR
ORDER BY cb.symbol, cb.ts
;;

CREATE OR REPLACE VIEW v_coinbase_premium_1d AS
WITH cb AS (SELECT symbol, CAST(ts AS DATE) AS date, close FROM cex_spot WHERE venue = 'coinbase' AND interval = '1d' AND symbol <> 'USDT'),
     u  AS (SELECT CAST(ts AS DATE) AS date, close AS usdt_usd FROM cex_spot WHERE venue = 'coinbase' AND interval = '1d' AND symbol = 'USDT'),
     bn AS (SELECT symbol, CAST(ts AS DATE) AS date, close FROM cex_spot WHERE venue = 'binance' AND interval = '1d')
SELECT cb.symbol, cb.date, cb.close AS coinbase_usd, bn.close AS binance_usdt, u.usdt_usd,
       (cb.close / (bn.close * coalesce(u.usdt_usd, 1)) - 1) * 1e4 AS premium_bp
FROM cb JOIN bn USING (symbol, date) LEFT JOIN u USING (date)
WHERE cb.date < current_date   -- completed days only
ORDER BY cb.symbol, cb.date
;;

-- Open interest by exchange, daily (USD). Binance = USDT-margined perp; OKX = all its contracts for the coin;
-- Bybit = USDT perp; Hyperliquid = perp (snapshotted hourly, so history starts when collection started).
CREATE OR REPLACE VIEW v_oi_daily_by_venue AS
SELECT CAST("timestamp" AS DATE) AS date, 'binance' AS venue, replace(symbol, 'USDT', '') AS symbol,
       last(sum_open_interest_value ORDER BY "timestamp") AS oi_usd
FROM binance_1h WHERE symbol IN ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT') GROUP BY 1, 2, 3
UNION ALL
SELECT CAST(ts AS DATE), venue, symbol, last(oi_usd ORDER BY ts)
FROM deriv_oi WHERE (venue IN ('okx', 'bybit') AND interval = '1d') OR venue = 'hyperliquid' GROUP BY 1, 2, 3
;;

-- Funding by exchange, daily average, expressed per 8 hours in basis points (Hyperliquid pays hourly).
CREATE OR REPLACE VIEW v_funding_daily AS
SELECT CAST(ts AS DATE) AS date, venue, symbol, avg(funding_rate * 8 / interval_hours) * 1e4 AS funding_8h_bp
FROM deriv_funding GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
;;

-- CME futures positioning (weekly), converted from contracts to coins.
CREATE OR REPLACE VIEW v_cme_positioning AS
SELECT report_date, asset,
       sum(open_interest * units_per_contract) AS oi_coins,
       sum((lev_long - lev_short) * units_per_contract) AS lev_funds_net_coins,
       sum((am_long - am_short) * units_per_contract) AS asset_mgr_net_coins,
       sum((dealer_long - dealer_short) * units_per_contract) AS dealer_net_coins
FROM cftc_crypto GROUP BY 1, 2
ORDER BY 1, 2
