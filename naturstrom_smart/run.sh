#!/usr/bin/with-contenv bashio
set -e

export NS_LOG_LEVEL="$(bashio::config 'log_level')"
export NS_RUN_HOUR="$(bashio::config 'run_hour')"
export NS_RUN_MINUTE="$(bashio::config 'run_minute')"
export NS_TIMEZONE="$(bashio::config 'timezone')"
export NS_PRICE_SOURCE="$(bashio::config 'price_source')"
export TZ="${NS_TIMEZONE}"
export NS_DATA_DIR="/data"
export NS_PORT="8099"

bashio::log.info "Starte naturstrom smart Vorschau (Lauf taeglich ${NS_RUN_HOUR}:${NS_RUN_MINUTE} ${NS_TIMEZONE})"

exec python3 -m nsforecast
