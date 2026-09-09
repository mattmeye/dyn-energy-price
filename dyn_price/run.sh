#!/usr/bin/with-contenv bashio
set -e

export DP_LOG_LEVEL="$(bashio::config 'log_level')"
export DP_RUN_HOUR="$(bashio::config 'run_hour')"
export DP_RUN_MINUTE="$(bashio::config 'run_minute')"
export DP_TIMEZONE="$(bashio::config 'timezone')"
export DP_PRICE_SOURCE="$(bashio::config 'price_source')"
export TZ="${DP_TIMEZONE}"
export DP_DATA_DIR="/data"
export DP_PORT="8099"

bashio::log.info "Starte Dynamischer Strompreis (Lauf täglich ${DP_RUN_HOUR}:${DP_RUN_MINUTE} ${DP_TIMEZONE})"

exec python3 -m dynprice
