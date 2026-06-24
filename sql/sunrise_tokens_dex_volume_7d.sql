-- Source query for dune.<team>.result_sunrise_tokens_dex_volume_7d
-- Seven-day buckets are anchored on 2025-10-10 and include data through current_date.
WITH token_mints(token_mint_address) AS (
    VALUES
        ('CrAr4RRJMBVwRsZtT62pEhfA9H5utymC2mVx8e7FreP2'),
        ('suifhC9gU1VbJAPYPTBkHJyyyStKGLLYPVDTmPoqbvA'),
        ('megaA5QDK1qLXtjpvg9oCFMvxT9d5BCMrVTBddnM5kV'),
        ('chipCAT7vi5CZtbZsn9z7iMPXvFwyAnKz3QFu8XVuHm'),
        ('BWsnyEa1XtsNRdgPaDoA1WVUonF7BBGZTd2zc72NQsWT'),
        ('6eftxVbSAunVEoxUWdGhPdxg5UdsJ8Wkwy5w5YFuxouw'),
        ('avaxGHCq3T7hoxd73oY2KY9hJSTaeMibXvHy5KNzh5D'),
        ('taoC6xyv2v8tDLcev4uaGUgV4vdQsWJrGft2kcBRrBY'),
        ('98sMhvDwXj1RQi5c5Mndm3vPe9cBqPrbLaufMXFNMh5g'),
        ('EicWvteVi2fWepEzS3FYWsnuPoP6caZfjnKqNvydLjCH'),
        ('SPCXxcqXj6e5dJDVNovHN8744zkbhM2bYudU45BimGb'),
        ('uniHfuPhEQSrtpzXpJZDCSq53yaejKKpNhFUiKoHKHV'),
        ('AavE1kKKnesPw4MuRJmJ9jZs9QzEE8CPxQ3ViczUDfc1'),
        ('72QvBVwpxqmheEPfaCwWSWqEFsUy3rhWt6JhQBMNTwD1'),
        ('Bi11Je4MH3PpyCNiuTAepRbsA5U6DK3DJy79ZFthddX'),
        ('inxKXw9V2NDZE7hDijzpJaKKUb97NEPJDTCEEiYg4yY'),
        ('BPxxfRCXkUVhig4HS1Lh7kZqV6SPJhzfEk4x6fVBjPCy'),
        ('MUxEsUKSMACyw5fZf68wxf5FLnZVhtU9CwH8uNNGay1'),
        ('SNDKbwMUQvZhnLnxLduradgLHG5KrPuKwpnrkkGRhfH')
),
sunrise_tokens AS (
    SELECT
        tm.token_mint_address,
        COALESCE(NULLIF(tf.symbol, ''), CONCAT('unknown_', SUBSTR(tm.token_mint_address, 1, 8))) AS symbol
    FROM token_mints AS tm
    LEFT JOIN tokens_solana.fungible AS tf
        ON tf.token_mint_address = tm.token_mint_address
),
trade_sides AS (
    SELECT
        dt.block_date,
        st.token_mint_address,
        st.symbol,
        dt.amount_usd,
        'sold' AS side
    FROM dex_solana.trades AS dt
    INNER JOIN sunrise_tokens AS st
        ON dt.token_sold_mint_address = st.token_mint_address
    WHERE dt.block_date >= DATE '2025-10-10'
        AND dt.block_date <= CURRENT_DATE
        AND dt.amount_usd > 0

    UNION ALL

    SELECT
        dt.block_date,
        st.token_mint_address,
        st.symbol,
        dt.amount_usd,
        'bought' AS side
    FROM dex_solana.trades AS dt
    INNER JOIN sunrise_tokens AS st
        ON dt.token_bought_mint_address = st.token_mint_address
    WHERE dt.block_date >= DATE '2025-10-10'
        AND dt.block_date <= CURRENT_DATE
        AND dt.amount_usd > 0
        AND dt.token_bought_mint_address <> dt.token_sold_mint_address
),
bucketed_trade_sides AS (
    SELECT
        DATE_ADD(
            'day',
            7 * CAST(FLOOR(DATE_DIFF('day', DATE '2025-10-10', block_date) / 7) AS integer),
            DATE '2025-10-10'
        ) AS bucket_start_date,
        token_mint_address,
        symbol,
        amount_usd,
        side
    FROM trade_sides
)
SELECT
    bucket_start_date,
    LEAST(DATE_ADD('day', 6, bucket_start_date), CURRENT_DATE) AS bucket_end_date,
    token_mint_address,
    symbol,
    CAST(SUM(amount_usd) AS double) AS volume_usd,
    CAST(SUM(CASE WHEN side = 'bought' THEN amount_usd ELSE 0 END) AS double) AS bought_volume_usd,
    CAST(SUM(CASE WHEN side = 'sold' THEN amount_usd ELSE 0 END) AS double) AS sold_volume_usd,
    COUNT(*) AS trade_side_count,
    CURRENT_TIMESTAMP AS refreshed_at
FROM bucketed_trade_sides
GROUP BY 1, 2, 3, 4
