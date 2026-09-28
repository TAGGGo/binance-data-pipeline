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
