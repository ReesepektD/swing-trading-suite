"""
Camillo Social Arbitrage — Live Dashboard

Run:
    python3 dashboard/app.py
    open http://localhost:5050
"""

import os
import sys
import sqlite3
import json
import time
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "camillo_bot.db")

TRADING_RULES = {
    "entry": [
        {"label": "BUY",   "threshold": "≥ 70", "color": "#22c55e", "action": "Place order"},
        {"label": "WATCH", "threshold": "50–69", "color": "#eab308", "action": "Monitor, no order"},
        {"label": "PASS",  "threshold": "< 50",  "color": "#6b7280", "action": "Skip"},
    ],
    "factors": [
        {"name": "Trend Velocity", "weight": 30, "color": "#3b82f6",
         "desc": "Google Trends slope — is search interest accelerating?"},
        {"name": "Analyst Gap",    "weight": 25, "color": "#f97316",
         "desc": "Fewer analysts = bigger information gap = higher score"},
        {"name": "Reddit Buzz",    "weight": 25, "color": "#a855f7",
         "desc": "Organic social chatter from non-investors"},
        {"name": "Price Lag",      "weight": 20, "color": "#14b8a6",
         "desc": "Trend rising but stock price hasn't caught up yet"},
    ],
    "sizing": {
        "buy_pct":       8,
        "watch_pct":     4,
        "max_positions": 12,
        "min_order":     50,
        "scale_note":    "Order size scales with score: score 85 BUY on $100k → 8% × 0.85 = $6,800",
    },
    "risk": [
        {"priority": 1, "rule": "Stop Loss",         "trigger": "Down 15% from entry",     "action": "Sell full position",  "color": "#ef4444"},
        {"priority": 2, "rule": "Max Hold",          "trigger": "Held 26 weeks",            "action": "Sell full position",  "color": "#f97316"},
        {"priority": 3, "rule": "Take Profit (½)",   "trigger": "Up 100% from entry",       "action": "Sell 50%, let rest run", "color": "#22c55e"},
        {"priority": 4, "rule": "Trend Peak",        "trigger": "Google Trends score < 5",  "action": "Sell full position",  "color": "#eab308"},
        {"priority": 5, "rule": "Low Score",         "trigger": "Re-scan score drops < 40", "action": "Sell full position",  "color": "#6b7280"},
    ],
    "portfolio_halt": {
        "trigger": "Portfolio down 20% from peak equity",
        "effect":  "Pause all new entries (existing positions still managed)",
    },
    "schedule": [
        {"time": "09:45 ET", "action": "Morning scan — score watchlist, place entries"},
        {"time": "12:00 ET", "action": "Midday exit check"},
        {"time": "15:30 ET", "action": "End-of-day exit check"},
        {"time": "Weekly",   "action": "Google Trends re-check on every open position"},
    ],
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_positions():
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM positions ORDER BY score_at_entry DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_trade_log(limit=20):
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM trade_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def enrich_with_live_prices(positions):
    if not positions:
        return positions
    try:
        from camillo_bot.yahoo_data import get_client
        ydc = get_client()
        tickers = [p["ticker"] for p in positions]
        quotes = ydc.get_quotes(tickers)
        for p in positions:
            q = quotes.get(p["ticker"])
            if q:
                p["current_price"] = q.last_price
                p["change_pct"]    = round(q.change_pct * 100, 2)
                entry = p.get("entry_price", 0) or 0
                if entry > 0:
                    p["unrealized_pct"] = round((q.last_price / entry - 1) * 100, 2)
                    p["unrealized_pl"]  = round((q.last_price - entry) * (p.get("qty") or 0), 2)
                else:
                    p["unrealized_pct"] = 0
                    p["unrealized_pl"]  = 0
            else:
                p["current_price"] = p.get("entry_price", 0)
                p["change_pct"]    = 0
                p["unrealized_pct"] = 0
                p["unrealized_pl"]  = 0
    except Exception as exc:
        for p in positions:
            p["current_price"]  = p.get("entry_price", 0)
            p["change_pct"]     = 0
            p["unrealized_pct"] = 0
            p["unrealized_pl"]  = 0
    return positions


@app.route("/api/data")
def api_data():
    positions = get_positions()
    positions = enrich_with_live_prices(positions)
    top3      = sorted(positions, key=lambda p: p.get("unrealized_pct", 0), reverse=True)[:3]
    log       = get_trade_log()

    total_pl  = sum(p.get("unrealized_pl", 0) for p in positions)
    total_val = sum((p.get("current_price", 0) or 0) * (p.get("qty") or 0) for p in positions)

    return jsonify({
        "positions":    positions,
        "top3":         top3,
        "trade_log":    log,
        "summary": {
            "open_positions": len(positions),
            "max_positions":  12,
            "total_pl":       round(total_pl, 2),
            "total_value":    round(total_val, 2),
        },
        "rules":        TRADING_RULES,
        "public_url":   os.environ.get("CAMILLO_DASHBOARD_URL", ""),
        "updated_at":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Camillo Social Arbitrage Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg:      #0f1117;
    --surface: #1a1d27;
    --card:    #21253a;
    --border:  #2d3150;
    --text:    #e2e8f0;
    --muted:   #8892a4;
    --green:   #22c55e;
    --yellow:  #eab308;
    --red:     #ef4444;
    --blue:    #3b82f6;
    --orange:  #f97316;
    --purple:  #a855f7;
    --teal:    #14b8a6;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'SF Pro Display', -apple-system, sans-serif; min-height: 100vh; }

  /* ── Layout ── */
  .header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 16px 32px; display: flex; align-items: center; justify-content: space-between; }
  .header h1 { font-size: 1.25rem; font-weight: 700; letter-spacing: -0.02em; }
  .header h1 span { color: var(--green); }
  .badge { background: var(--card); border: 1px solid var(--border); border-radius: 20px; padding: 4px 12px; font-size: 0.75rem; color: var(--muted); }
  .updated { font-size: 0.7rem; color: var(--muted); }

  .main { padding: 24px 32px; max-width: 1400px; margin: 0 auto; display: flex; flex-direction: column; gap: 24px; }

  /* ── Summary bar ── */
  .summary-bar { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
  .stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }
  .stat-card .label { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin-bottom: 8px; }
  .stat-card .value { font-size: 1.75rem; font-weight: 700; letter-spacing: -0.03em; }
  .stat-card .sub   { font-size: 0.75rem; color: var(--muted); margin-top: 4px; }
  .green { color: var(--green); }
  .red   { color: var(--red); }
  .yellow{ color: var(--yellow); }

  /* ── Grid ── */
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
  .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
  .full   { grid-column: 1 / -1; }

  /* ── Card ── */
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
  .card-header { padding: 16px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
  .card-header h2 { font-size: 0.875rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
  .card-body { padding: 20px; }

  /* ── Position cards ── */
  .pos-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
  .pos-top { display: flex; justify-content: space-between; align-items: flex-start; }
  .pos-ticker { font-size: 1.4rem; font-weight: 800; letter-spacing: -0.02em; }
  .pos-signal { font-size: 0.65rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; padding: 3px 8px; border-radius: 4px; background: rgba(34,197,94,0.15); color: var(--green); border: 1px solid rgba(34,197,94,0.3); }
  .pos-prices { display: flex; gap: 16px; }
  .pos-price-block .plabel { font-size: 0.65rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
  .pos-price-block .pval   { font-size: 1rem; font-weight: 600; }
  .pos-bar-row { display: flex; align-items: center; gap: 8px; }
  .pos-bar-track { flex: 1; height: 4px; background: var(--border); border-radius: 2px; }
  .pos-bar-fill  { height: 4px; border-radius: 2px; transition: width 0.4s; }
  .pos-score-label { font-size: 0.7rem; color: var(--muted); }
  .pos-pl { font-size: 0.875rem; font-weight: 600; }
  .pos-meta { font-size: 0.7rem; color: var(--muted); }
  .empty-state { text-align: center; padding: 32px; color: var(--muted); font-size: 0.875rem; }

  /* ── Factor chart ── */
  .chart-wrap { position: relative; height: 220px; }

  /* ── Rules table ── */
  .rules-table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
  .rules-table th { text-align: left; padding: 8px 12px; font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); border-bottom: 1px solid var(--border); }
  .rules-table td { padding: 10px 12px; border-bottom: 1px solid rgba(45,49,80,0.5); }
  .rules-table tr:last-child td { border-bottom: none; }
  .priority-dot { display: inline-flex; width: 20px; height: 20px; border-radius: 50%; align-items: center; justify-content: center; font-size: 0.65rem; font-weight: 700; }

  /* ── Entry thresholds ── */
  .threshold-row { display: flex; gap: 12px; margin-bottom: 16px; }
  .threshold-pill { flex: 1; padding: 12px; border-radius: 8px; text-align: center; border: 1px solid; }
  .threshold-pill .t-label { font-size: 1rem; font-weight: 800; }
  .threshold-pill .t-range { font-size: 0.7rem; margin: 2px 0; opacity: 0.8; }
  .threshold-pill .t-action { font-size: 0.65rem; opacity: 0.65; }

  /* ── Schedule ── */
  .schedule-item { display: flex; gap: 16px; align-items: flex-start; padding: 10px 0; border-bottom: 1px solid rgba(45,49,80,0.5); }
  .schedule-item:last-child { border-bottom: none; }
  .schedule-time { font-size: 0.75rem; font-weight: 600; color: var(--blue); min-width: 80px; }
  .schedule-action { font-size: 0.8rem; color: var(--text); }

  /* ── Trade log ── */
  .log-row { display: flex; gap: 12px; align-items: center; padding: 8px 0; border-bottom: 1px solid rgba(45,49,80,0.4); font-size: 0.75rem; }
  .log-row:last-child { border-bottom: none; }
  .log-side { font-weight: 700; min-width: 36px; }
  .log-ticker { font-weight: 600; min-width: 52px; }
  .log-ts { color: var(--muted); margin-left: auto; }
  .log-reason { color: var(--muted); font-size: 0.7rem; }

  /* ── Sizing info ── */
  .sizing-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 14px; }
  .sizing-item { background: var(--card); border-radius: 8px; padding: 12px; }
  .sizing-item .s-label { font-size: 0.65rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
  .sizing-item .s-val   { font-size: 1.1rem; font-weight: 700; margin-top: 4px; }
  .sizing-note { font-size: 0.72rem; color: var(--muted); background: var(--card); border-radius: 8px; padding: 10px 12px; border-left: 3px solid var(--blue); }

  /* ── Halt banner ── */
  .halt-banner { background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3); border-radius: 8px; padding: 12px 16px; margin-top: 14px; }
  .halt-banner .h-trigger { font-size: 0.75rem; font-weight: 600; color: var(--red); }
  .halt-banner .h-effect  { font-size: 0.72rem; color: var(--muted); margin-top: 3px; }

  /* ── Pulse dot ── */
  .live-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--green); box-shadow: 0 0 0 0 rgba(34,197,94,0.4); animation: pulse 2s infinite; margin-right: 6px; }
  @keyframes pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(34,197,94,0.4); } 50% { box-shadow: 0 0 0 6px rgba(34,197,94,0); } }

  /* ── Bottom nav (mobile only) ── */
  .bottom-nav { display: none; position: fixed; bottom: 0; left: 0; right: 0; background: var(--surface); border-top: 1px solid var(--border); z-index: 100; padding: 0; safe-area-inset-bottom: env(safe-area-inset-bottom); }
  .bottom-nav-inner { display: flex; justify-content: space-around; padding: 6px 0 calc(6px + env(safe-area-inset-bottom, 0px)); }
  .nav-btn { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 3px; padding: 6px 4px; border: none; background: none; color: var(--muted); cursor: pointer; font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.05em; -webkit-tap-highlight-color: transparent; transition: color 0.15s; }
  .nav-btn svg { width: 22px; height: 22px; stroke-width: 1.8; }
  .nav-btn.active { color: var(--blue); }

  /* ── Table scroll containers ── */
  .table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }

  /* ── Tablet ── */
  @media (max-width: 900px) {
    .summary-bar { grid-template-columns: 1fr 1fr; }
    .grid-2      { grid-template-columns: 1fr; }
    .grid-3      { grid-template-columns: 1fr; }
  }

  /* ── Mobile ── */
  @media (max-width: 600px) {
    .header { padding: 12px 16px; }
    .header h1 { font-size: 1rem; }
    .header h1 .subtitle { display: none; }
    .updated { display: none; }

    #public-bar { padding: 8px 16px; font-size: 0.72rem; flex-wrap: wrap; gap: 4px; }

    .main { padding: 12px 12px 80px; gap: 14px; }

    .summary-bar { grid-template-columns: 1fr 1fr; gap: 10px; }
    .stat-card { padding: 14px 12px; border-radius: 10px; }
    .stat-card .value { font-size: 1.35rem; }
    .stat-card .sub { font-size: 0.65rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    .card-header { padding: 12px 14px; }
    .card-body { padding: 12px; }

    .pos-card { padding: 12px; }
    .pos-ticker { font-size: 1.2rem; }
    .pos-prices { flex-wrap: wrap; gap: 10px; }
    .pos-price-block { min-width: 70px; }

    .chart-wrap { height: 180px; }

    .threshold-row { gap: 8px; }
    .threshold-pill { padding: 10px 6px; }
    .threshold-pill .t-label { font-size: 0.85rem; }
    .threshold-pill .t-action { display: none; }

    .sizing-grid { grid-template-columns: 1fr 1fr; gap: 8px; }
    .sizing-item { padding: 10px; }
    .sizing-item .s-val { font-size: 1rem; }

    .rules-table td, .rules-table th { padding: 8px 8px; font-size: 0.72rem; }
    .rules-table th:nth-child(4), .rules-table td:nth-child(4) { display: none; }

    .schedule-item { gap: 10px; }
    .schedule-time { min-width: 68px; font-size: 0.7rem; }
    .schedule-action { font-size: 0.75rem; }

    .log-row { flex-wrap: wrap; gap: 6px; }
    .log-reason, .log-ts { font-size: 0.65rem; }

    .empty-state { padding: 20px; font-size: 0.8rem; }

    .bottom-nav { display: block; }

    /* hide sections when not active on mobile */
    .section { display: none; }
    .section.active { display: block; }
    .summary-bar { display: grid !important; }
  }
</style>
</head>
<body>

<div class="header">
  <h1>Camillo <span class="subtitle">Social Arbitrage</span> Dashboard</h1>
  <div style="display:flex;align-items:center;gap:10px;">
    <span class="badge"><span class="live-dot"></span>Live</span>
    <span class="updated" id="updated-at">Loading…</span>
  </div>
</div>
<div id="public-bar" style="display:none;background:#1e3a5f;border-bottom:1px solid #2d5a8e;padding:8px 32px;font-size:0.78rem;color:#93c5fd;align-items:center;gap:10px;">
  <span>&#128279;</span>
  <a id="public-url-link" href="#" target="_blank" style="color:#60a5fa;font-weight:600;text-decoration:none;word-break:break-all;"></a>
</div>

<div class="main">

  <!-- Always-visible summary bar -->
  <div class="summary-bar">
    <div class="stat-card">
      <div class="label">Positions</div>
      <div class="value" id="open-pos">—</div>
      <div class="sub" id="pos-sub">of 12 max</div>
    </div>
    <div class="stat-card">
      <div class="label">Market Value</div>
      <div class="value" id="total-val">—</div>
      <div class="sub">open positions</div>
    </div>
    <div class="stat-card">
      <div class="label">Unrealized P&L</div>
      <div class="value" id="total-pl">—</div>
      <div class="sub">all positions</div>
    </div>
    <div class="stat-card">
      <div class="label">Watchlist</div>
      <div class="value green">6</div>
      <div class="sub">CELH ONON BIRK DUOL CAVA BROS</div>
    </div>
  </div>

  <!-- SECTION: Positions -->
  <div id="section-positions" class="section active">

    <div class="grid-2">
      <div class="card">
        <div class="card-header">
          <h2>Top 3 Positions</h2>
          <span class="badge" style="font-size:0.65rem;">by unrealized gain</span>
        </div>
        <div class="card-body">
          <div id="top3-container">
            <div class="empty-state">No open positions yet.<br><code style="font-size:0.75rem;">python3 run_bot.py --mode paper --scan-now</code></div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h2>4-Factor Scoring Weights</h2></div>
        <div class="card-body">
          <div class="chart-wrap">
            <canvas id="factorChart"></canvas>
          </div>
          <div style="margin-top:16px;display:flex;flex-direction:column;gap:8px;" id="factor-legend"></div>
        </div>
      </div>
    </div>

  </div>

  <!-- SECTION: Rules -->
  <div id="section-rules" class="section">

    <div class="grid-2">
      <div class="card">
        <div class="card-header"><h2>Entry Thresholds</h2></div>
        <div class="card-body">
          <div class="threshold-row" id="thresholds"></div>
          <div style="margin-bottom:12px;font-size:0.75rem;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:0.06em;">Position Sizing</div>
          <div class="sizing-grid" id="sizing-grid"></div>
          <div class="sizing-note" id="sizing-note"></div>
          <div class="halt-banner" id="halt-banner" style="display:none;"></div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h2>Exit Rules</h2>
          <span class="badge" style="font-size:0.65rem;">priority order</span>
        </div>
        <div class="table-scroll" style="padding:0;">
          <table class="rules-table">
            <thead><tr><th>#</th><th>Rule</th><th>Trigger</th><th>Action</th></tr></thead>
            <tbody id="risk-rows"></tbody>
          </table>
        </div>
      </div>
    </div>

  </div>

  <!-- SECTION: Schedule + Log -->
  <div id="section-log" class="section">

    <div class="grid-2">
      <div class="card">
        <div class="card-header"><h2>Scan Schedule</h2></div>
        <div class="card-body" id="schedule-body"></div>
      </div>

      <div class="card">
        <div class="card-header">
          <h2>Recent Trades</h2>
          <span class="badge" id="log-count" style="font-size:0.65rem;">0 trades</span>
        </div>
        <div class="card-body" id="log-body">
          <div class="empty-state">No trades yet.</div>
        </div>
      </div>
    </div>

  </div>

</div>

<!-- Mobile bottom nav -->
<nav class="bottom-nav" aria-label="Dashboard navigation">
  <div class="bottom-nav-inner">
    <button class="nav-btn active" onclick="showSection('positions',this)" aria-label="Positions">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 3h18v18H3zM3 9h18M9 21V9"/></svg>
      Positions
    </button>
    <button class="nav-btn" onclick="showSection('rules',this)" aria-label="Rules">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"/></svg>
      Rules
    </button>
    <button class="nav-btn" onclick="showSection('log',this)" aria-label="Activity">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
      Activity
    </button>
  </div>
</nav>

<script>
let factorChart = null;

function fmt(n, prefix='$') {
  if (n == null || isNaN(n)) return '—';
  const abs = Math.abs(n);
  const s = abs >= 1000 ? prefix + abs.toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2}) : prefix + abs.toFixed(2);
  return n < 0 ? '-' + s : s;
}
function pct(n) {
  if (n == null || isNaN(n)) return '—';
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}
function plClass(n) { return n > 0 ? 'green' : n < 0 ? 'red' : ''; }
function scoreColor(s) {
  if (s >= 70) return '#22c55e';
  if (s >= 50) return '#eab308';
  return '#6b7280';
}

function renderSummary(summary) {
  document.getElementById('open-pos').textContent = summary.open_positions;
  document.getElementById('pos-sub').textContent  = `of ${summary.max_positions} max`;
  document.getElementById('total-val').textContent = fmt(summary.total_value);
  const plEl = document.getElementById('total-pl');
  plEl.textContent  = fmt(summary.total_pl);
  plEl.className    = 'value ' + plClass(summary.total_pl);
}

function renderTop3(top3) {
  const el = document.getElementById('top3-container');
  if (!top3 || top3.length === 0) {
    el.innerHTML = '<div class="empty-state">No open positions yet.<br>Run <code>python3 run_bot.py --mode paper --scan-now</code> to enter positions.</div>';
    return;
  }
  el.innerHTML = top3.map(p => {
    const uPct  = p.unrealized_pct ?? 0;
    const uPl   = p.unrealized_pl  ?? 0;
    const cur   = p.current_price  ?? p.entry_price ?? 0;
    const score = p.score_at_entry ?? 0;
    const weeks = p.entry_date ? Math.floor((Date.now()/86400000 - new Date(p.entry_date).getTime()/86400000) / 7) : 0;
    const barW  = Math.min(score, 100);
    const barColor = scoreColor(score);
    return `
    <div class="pos-card" style="margin-bottom:12px;">
      <div class="pos-top">
        <span class="pos-ticker">${p.ticker}</span>
        <span class="pos-signal">${p.signal || 'BUY'}</span>
      </div>
      <div class="pos-prices">
        <div class="pos-price-block">
          <div class="plabel">Entry</div>
          <div class="pval">$${(p.entry_price||0).toFixed(2)}</div>
        </div>
        <div class="pos-price-block">
          <div class="plabel">Current</div>
          <div class="pval">$${cur.toFixed(2)}</div>
        </div>
        <div class="pos-price-block">
          <div class="plabel">P&L</div>
          <div class="pval ${plClass(uPct)}">${pct(uPct)}</div>
        </div>
        <div class="pos-price-block">
          <div class="plabel">$P&L</div>
          <div class="pval ${plClass(uPl)}">${fmt(uPl)}</div>
        </div>
      </div>
      <div class="pos-bar-row">
        <div class="pos-bar-track"><div class="pos-bar-fill" style="width:${barW}%;background:${barColor};"></div></div>
        <span class="pos-score-label">Score ${score.toFixed(0)}</span>
      </div>
      <div class="pos-meta">Entered ${p.entry_date || '—'} · ${weeks}w held · ${(p.qty||0).toFixed(4)} shares · Today ${pct(p.change_pct ?? 0)}</div>
    </div>`;
  }).join('');
}

function renderFactorChart(factors) {
  const ctx = document.getElementById('factorChart').getContext('2d');
  if (factorChart) factorChart.destroy();
  factorChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels:   factors.map(f => f.name),
      datasets: [{
        data:            factors.map(f => f.weight),
        backgroundColor: factors.map(f => f.color + 'cc'),
        borderColor:     factors.map(f => f.color),
        borderWidth: 2,
        hoverOffset: 8,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      cutout: '68%',
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: { label: ctx => ` ${ctx.label}: ${ctx.parsed}%` }
        }
      }
    }
  });

  const legend = document.getElementById('factor-legend');
  legend.innerHTML = factors.map(f => `
    <div style="display:flex;align-items:center;gap:8px;font-size:0.78rem;">
      <span style="width:10px;height:10px;border-radius:50%;background:${f.color};flex-shrink:0;"></span>
      <span style="color:var(--text);font-weight:600;">${f.name}</span>
      <span style="color:var(--muted);margin-left:auto;">${f.weight}%</span>
      <span style="color:var(--muted);font-size:0.68rem;max-width:160px;text-align:right;">${f.desc}</span>
    </div>
  `).join('');
}

function renderThresholds(entry) {
  const el = document.getElementById('thresholds');
  el.innerHTML = entry.map(e => `
    <div class="threshold-pill" style="border-color:${e.color}33;background:${e.color}11;">
      <div class="t-label" style="color:${e.color};">${e.label}</div>
      <div class="t-range" style="color:${e.color};">${e.threshold}</div>
      <div class="t-action">${e.action}</div>
    </div>
  `).join('');
}

function renderSizing(sizing) {
  document.getElementById('sizing-grid').innerHTML = `
    <div class="sizing-item"><div class="s-label">BUY allocation</div><div class="s-val green">Up to ${sizing.buy_pct}%</div></div>
    <div class="sizing-item"><div class="s-label">WATCH allocation</div><div class="s-val yellow">Up to ${sizing.watch_pct}%</div></div>
    <div class="sizing-item"><div class="s-label">Max positions</div><div class="s-val">${sizing.max_positions}</div></div>
    <div class="sizing-item"><div class="s-label">Min order size</div><div class="s-val">$${sizing.min_order}</div></div>
  `;
  document.getElementById('sizing-note').textContent = sizing.scale_note;
}

function renderRisk(risk, halt) {
  const tbody = document.getElementById('risk-rows');
  tbody.innerHTML = risk.map(r => `
    <tr>
      <td><span class="priority-dot" style="background:${r.color}22;color:${r.color};">${r.priority}</span></td>
      <td style="font-weight:600;">${r.rule}</td>
      <td style="color:var(--muted);">${r.trigger}</td>
      <td>${r.action}</td>
    </tr>
  `).join('');

  const hb = document.getElementById('halt-banner');
  if (halt) {
    hb.style.display = 'block';
    hb.innerHTML = `
      <div class="h-trigger">⚠ Portfolio Kill Switch</div>
      <div class="h-effect">Trigger: ${halt.trigger}</div>
      <div class="h-effect" style="margin-top:2px;">Effect: ${halt.effect}</div>
    `;
  }
}

function renderSchedule(schedule) {
  document.getElementById('schedule-body').innerHTML = schedule.map(s => `
    <div class="schedule-item">
      <span class="schedule-time">${s.time}</span>
      <span class="schedule-action">${s.action}</span>
    </div>
  `).join('');
}

function renderLog(log) {
  const el = document.getElementById('log-body');
  document.getElementById('log-count').textContent = log.length + ' trades';
  if (!log || log.length === 0) {
    el.innerHTML = '<div class="empty-state">No trades yet.</div>';
    return;
  }
  el.innerHTML = log.map(t => {
    const isBuy = (t.side || '').toLowerCase() === 'buy';
    return `
    <div class="log-row">
      <span class="log-side" style="color:${isBuy ? 'var(--green)' : 'var(--red)'};">${(t.side||'').toUpperCase()}</span>
      <span class="log-ticker">${t.ticker}</span>
      <span>${t.qty ? parseFloat(t.qty).toFixed(4) : '—'} sh</span>
      <span>@ $${t.price ? parseFloat(t.price).toFixed(2) : '—'}</span>
      <span class="log-reason">${(t.reason||'').replace(/_/g,' ')}</span>
      <span class="log-ts">${(t.timestamp||'').slice(0,16)}</span>
    </div>`;
  }).join('');
}

async function refresh() {
  try {
    const res  = await fetch('/api/data');
    const data = await res.json();

    document.getElementById('updated-at').textContent = 'Updated ' + data.updated_at;

    const bar  = document.getElementById('public-bar');
    const link = document.getElementById('public-url-link');
    if (data.public_url) {
      bar.style.display = 'flex';
      link.href        = data.public_url;
      link.textContent = data.public_url;
    } else {
      bar.style.display = 'none';
    }

    renderSummary(data.summary);
    renderTop3(data.top3);
    renderFactorChart(data.rules.factors);
    renderThresholds(data.rules.entry);
    renderSizing(data.rules.sizing);
    renderRisk(data.rules.risk, data.rules.portfolio_halt);
    renderSchedule(data.rules.schedule);
    renderLog(data.trade_log);
  } catch(e) {
    console.error('Refresh failed:', e);
  }
}

function showSection(name, btn) {
  // Only applies on mobile (≤600px) — on desktop all sections are always visible
  if (window.innerWidth > 600) return;
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.getElementById('section-' + name).classList.add('active');
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// On resize to desktop, make all sections visible again
window.addEventListener('resize', () => {
  if (window.innerWidth > 600) {
    document.querySelectorAll('.section').forEach(s => s.style.display = '');
  }
});

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


def start_ngrok(port: int) -> str:
    """Start an ngrok tunnel and return the public HTTPS URL (or empty string on failure)."""
    import subprocess, shutil, time as _time, urllib.request

    ngrok_bin = shutil.which("ngrok") or os.path.expanduser("~/bin/ngrok")
    if not os.path.exists(ngrok_bin):
        print("  ✗ ngrok not found — run without --public or install ngrok")
        return ""

    proc = subprocess.Popen(
        [ngrok_bin, "http", str(port), "--log=stdout"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # Poll ngrok's local API until the tunnel URL appears
    for _ in range(20):
        _time.sleep(0.5)
        try:
            with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=1) as r:
                data = json.loads(r.read())
            for t in data.get("tunnels", []):
                if t.get("proto") == "https":
                    return t["public_url"]
        except Exception:
            pass

    proc.terminate()
    print("  ✗ ngrok tunnel did not start in time")
    return ""


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port",   type=int, default=5050)
    parser.add_argument("--host",   default="127.0.0.1")
    parser.add_argument("--public", action="store_true",
                        help="Expose dashboard publicly via ngrok and set CAMILLO_DASHBOARD_URL")
    args = parser.parse_args()

    public_url = ""
    if args.public:
        print("  Starting ngrok tunnel…")
        public_url = start_ngrok(args.port)
        if public_url:
            os.environ["CAMILLO_DASHBOARD_URL"] = public_url
            print(f"  ✓ Public URL  → {public_url}")
            print(f"  ✓ Emails will include a 'View Live Dashboard' button")

    local_url = f"http://{args.host}:{args.port}"
    print(f"\n  Camillo Dashboard → {local_url}")
    if public_url:
        print(f"  Public link    → {public_url}\n")
    else:
        print()

    app.run(host=args.host, port=args.port, debug=False)
