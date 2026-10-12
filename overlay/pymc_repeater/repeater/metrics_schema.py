"""Shared aggregation definitions for metrics retention and history queries.

This module contains only schema data; importing it does not initialize the
retention worker or its database connections. Queries use reset-aware deltas
for cumulative channel counters; retention keeps their legacy summary schema.
"""

from copy import deepcopy

# Table name -> raw timestamp column, grouping columns and (SQL, alias) pairs.
QUERY_TABLES: dict[str, dict] = {
    "packet_activity": {
        "ts_col": "timestamp",
        "group_cols": ["channel_id"],
        "agg_cols": [
            ("COUNT(*)",        "sample_count"),
            ("SUM(rx_count)",   "total_rx_count"),
            ("SUM(tx_count)",   "total_tx_count"),
        ],
    },
    "noise_floor_history": {
        "ts_col": "timestamp",
        "group_cols": ["channel_id"],
        "agg_cols": [
            ("COUNT(*)",                    "sample_count"),
            ("AVG(noise_floor_dbm)",        "avg_noise_floor_dbm"),
            ("MIN(noise_floor_dbm)",        "min_noise_floor_dbm"),
            ("MAX(noise_floor_dbm)",        "max_noise_floor_dbm"),
            ("SUM(samples_collected)",      "total_samples_collected"),
            ("SUM(samples_accepted)",       "total_samples_accepted"),
            ("MIN(min_rssi)",               "min_rssi"),
            ("MAX(max_rssi)",               "max_rssi"),
        ],
    },
    "channel_stats_history": {
        "ts_col": "timestamp",
        "group_cols": ["channel_id"],
        "agg_cols": [
            ("COUNT(*)",                                  "sample_count"),
            # Deltas are computed before time filtering and bucketing, so
            # a prior sample and transitions across buckets are preserved.
            ("SUM(delta_rx_count)",                       "total_rx_count"),
            ("AVG(avg_rssi)",                             "avg_rssi"),
            ("AVG(avg_snr)",                              "avg_snr"),
            ("SUM(delta_tx_count)",                       "total_tx_count"),
            ("SUM(delta_tx_failed)",                      "total_tx_failed"),
            ("SUM(delta_tx_airtime_ms)",                  "total_tx_airtime_ms"),
            ("SUM(delta_tx_bytes)",                       "total_tx_bytes"),
            ("SUM(delta_lbt_blocked)",                    "total_lbt_blocked"),
            ("SUM(delta_lbt_passed)",                     "total_lbt_passed"),
            ("AVG(noise_floor_dbm)",                      "avg_noise_floor_dbm"),
            ("AVG(tx_noisefloor_dbm)",                    "avg_tx_noisefloor_dbm"),
            ("AVG(lbt_last_rssi)",                        "avg_lbt_last_rssi"),
        ],
    },
    "dedup_events": {
        "ts_col": "ts",
        "group_cols": ["event_type", "source"],
        "agg_cols": [
            ("COUNT(*)",                 "sample_count"),
            ("COUNT(DISTINCT pkt_hash)", "unique_packets"),
            ("SUM(pkt_size)",            "total_bytes"),
        ],
    },
    "cad_events": {
        "ts_col": "timestamp",
        "group_cols": ["channel_id"],
        "agg_cols": [
            ("COUNT(*)",              "sample_count"),
            ("SUM(cad_clear)",        "total_cad_clear"),
            ("SUM(cad_detected)",     "total_cad_detected"),
            ("SUM(cad_skipped)",      "total_cad_skipped"),
            ("SUM(cad_hw_clear)",     "total_cad_hw_clear"),
            ("SUM(cad_hw_detected)",  "total_cad_hw_detected"),
            ("SUM(cad_sw_clear)",     "total_cad_sw_clear"),
            ("SUM(cad_sw_detected)",  "total_cad_sw_detected"),
            ("SUM(cad_busy_events)",      "total_cad_busy_events"),
        ],
    },
    "crc_error_rate": {
        "ts_col": "timestamp",
        "group_cols": ["channel_id"],
        "agg_cols": [
            ("COUNT(*)",                "sample_count"),
            ("SUM(crc_error_count)",    "total_crc_errors"),
            ("SUM(crc_disabled_count)", "total_crc_disabled"),
        ],
    },
    "packet_metrics": {
        "ts_col": "timestamp",
        "group_cols": ["channel_id", "direction"],
        "agg_cols": [
            ("COUNT(*)",        "sample_count"),
            ("AVG(rssi)",       "avg_rssi"),
            ("MIN(rssi)",       "min_rssi"),
            ("MAX(rssi)",       "max_rssi"),
            ("AVG(snr)",        "avg_snr"),
            ("MIN(snr)",        "min_snr"),
            ("MAX(snr)",        "max_snr"),
            ("AVG(airtime_ms)", "avg_airtime_ms"),
            ("SUM(airtime_ms)", "total_airtime_ms"),
            ("SUM(length)",     "total_bytes"),
            ("AVG(hop_count)",  "avg_hop_count"),
            ("SUM(CASE WHEN crc_ok=0 THEN 1 ELSE 0 END)", "crc_error_count"),
        ],
    },
    "origin_channel_stats": {
        "ts_col": "timestamp",
        "group_cols": ["channel_id"],
        "agg_cols": [
            ("COUNT(*)",    "sample_count"),
            ("SUM(count)",  "total_count"),
        ],
    },
}

# Must match the storage schema. These denominators are internal unless
# explicitly requested; sample_count is not a valid weight for nullable data.
for _cfg in QUERY_TABLES.values():
    _cfg["agg_cols"] += [
        (f"COUNT({expr[4:-1]})", f"{alias}_count")
        for expr, alias in _cfg["agg_cols"] if expr.startswith("AVG(")
    ]

# Legacy channel summaries used MAX-MIN. Keep their definitions for schema
# compatibility; current retention leaves cumulative snapshots raw and queries
# sum deltas computed before time filtering and bucketing.
_LEGACY_CHANNEL_AGGREGATES = {
    alias: f"MAX({column}) - MIN({column})"
    for alias, column in (
        ("total_rx_count", "rx_count"),
        ("total_tx_count", "tx_count"),
        ("total_tx_failed", "tx_failed"),
        ("total_tx_airtime_ms", "tx_airtime_ms"),
        ("total_tx_bytes", "tx_bytes"),
        ("total_lbt_blocked", "lbt_blocked"),
        ("total_lbt_passed", "lbt_passed"),
    )
}

# Preserve the retention order and keep its mutable definitions independent
# from the query registry.
DOWNSAMPLE_TABLES: list[dict] = []
for _table in (
    "packet_metrics", "dedup_events", "noise_floor_history", "cad_events",
    "channel_stats_history", "packet_activity", "crc_error_rate", "origin_channel_stats",
):
    _cfg = {"db": "repeater.db", "table": _table, **deepcopy(QUERY_TABLES[_table])}
    if _table == "channel_stats_history":
        _cfg["agg_cols"] = [
            (_LEGACY_CHANNEL_AGGREGATES.get(alias, expr), alias)
            for expr, alias in _cfg["agg_cols"]
        ]
    DOWNSAMPLE_TABLES.append(_cfg)
