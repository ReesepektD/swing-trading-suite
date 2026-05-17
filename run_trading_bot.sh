#!/bin/bash
# Wrapper for crontab — sets all env vars and runs trading_bot.py
# Usage: run_trading_bot.sh --premarket | --midday | --run | --trade | --monitor

export ALPACA_KEY="PK4AFNMPPZTAX77OAO6SRVL7Z4"
export ALPACA_SECRET="4ivwJT2XoE3eV3cWTwcKFPNKaLxvSfDVqrvyrW15RMtK"
export EMAIL_FROM="reesebot01@gmail.com"
export EMAIL_PASS="pomz zlyb yadr qade"

DIR="$(cd "$(dirname "$0")" && pwd)"
/usr/bin/python3 "$DIR/trading_bot.py" "$@"
