"""
Email notifier for the Camillo bot.

Sends scan reports and daily summaries via SMTP (Gmail by default).

Config via environment variables:
  CAMILLO_EMAIL_FROM      sender address (e.g. you@gmail.com)
  CAMILLO_EMAIL_TO        recipient address (can be same as FROM)
  CAMILLO_EMAIL_PASSWORD  Gmail App Password (not your login password)
                          → https://myaccount.google.com/apppasswords
  CAMILLO_EMAIL_SMTP      SMTP host (default: smtp.gmail.com)
  CAMILLO_EMAIL_PORT      SMTP port (default: 587)
  CAMILLO_DASHBOARD_URL   public dashboard URL included in every email
                          (set automatically by dashboard --public mode)
"""

import logging
import os
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

log = logging.getLogger(__name__)


class EmailNotifier:
    def __init__(
        self,
        from_addr:     str,
        to_addr:       str,
        password:      str,
        smtp_host:     str = "smtp.gmail.com",
        smtp_port:     int = 587,
        dashboard_url: str = "",
    ):
        self.from_addr     = from_addr
        self.to_addr       = to_addr
        self.password      = password
        self.smtp_host     = smtp_host
        self.smtp_port     = smtp_port
        self.dashboard_url = dashboard_url

    @classmethod
    def from_env(cls) -> Optional["EmailNotifier"]:
        """Return an EmailNotifier from env vars, or None if not configured."""
        from_addr = os.getenv("CAMILLO_EMAIL_FROM", "")
        to_addr   = os.getenv("CAMILLO_EMAIL_TO", from_addr)
        password  = os.getenv("CAMILLO_EMAIL_PASSWORD", "")
        if not (from_addr and password):
            return None
        return cls(
            from_addr     = from_addr,
            to_addr       = to_addr,
            password      = password,
            smtp_host     = os.getenv("CAMILLO_EMAIL_SMTP", "smtp.gmail.com"),
            smtp_port     = int(os.getenv("CAMILLO_EMAIL_PORT", "587")),
            dashboard_url = os.getenv("CAMILLO_DASHBOARD_URL", ""),
        )

    def send(self, subject: str, html: str, plain: str = "") -> bool:
        """Send an email. Returns True on success, False on failure."""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = self.from_addr
            msg["To"]      = self.to_addr

            if plain:
                msg.attach(MIMEText(plain, "plain"))
            msg.attach(MIMEText(html, "html"))

            ctx = ssl.create_default_context()
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.ehlo()
                server.starttls(context=ctx)
                server.login(self.from_addr, self.password)
                server.sendmail(self.from_addr, self.to_addr, msg.as_string())

            log.info("Email sent → %s  [%s]", self.to_addr, subject)
            return True
        except Exception as exc:
            log.warning("Email failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Formatted report builders
    # ------------------------------------------------------------------

    def send_scan_report(self, scan_results, positions, account_equity: float):
        """
        Send the morning scan report.

        scan_results: pandas DataFrame returned by scanner.scan_watchlist()
        positions:    list of DBPosition currently open
        """
        now     = datetime.now().strftime("%Y-%m-%d %H:%M")
        subject = f"Camillo Scan Report — {datetime.now().strftime('%b %d')}"

        # Build scan rows
        scan_rows = ""
        if scan_results is not None and not scan_results.empty:
            for _, row in scan_results.iterrows():
                sig    = row.get("Signal", "—")
                score  = row.get("Composite", 0)
                color  = "#22c55e" if sig == "BUY" else "#eab308" if sig == "WATCH" else "#6b7280"
                bg     = "rgba(34,197,94,0.08)" if sig == "BUY" else "rgba(234,179,8,0.08)" if sig == "WATCH" else ""
                scan_rows += f"""
                <tr style="background:{bg};">
                  <td style="padding:10px 14px;font-weight:700;font-size:1rem;">{row.get('Ticker','')}</td>
                  <td style="padding:10px 14px;"><span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:700;">{sig}</span></td>
                  <td style="padding:10px 14px;font-weight:600;color:{color};">{score:.1f}</td>
                  <td style="padding:10px 14px;color:#8892a4;">{row.get('TrendVel',0):.0f}</td>
                  <td style="padding:10px 14px;color:#8892a4;">{row.get('AnalystGap',0):.0f}</td>
                  <td style="padding:10px 14px;color:#8892a4;">{row.get('RedditBuzz',0):.0f}</td>
                  <td style="padding:10px 14px;color:#8892a4;">{row.get('PriceLag',0):.0f}</td>
                </tr>"""
        else:
            scan_rows = '<tr><td colspan="7" style="padding:16px;text-align:center;color:#8892a4;">No scan results</td></tr>'

        # Build position rows
        pos_rows = ""
        if positions:
            for p in positions:
                pct_held = ""
                try:
                    from camillo_bot.yahoo_data import get_client
                    cur = get_client().get_price(p.ticker)
                    ret = (cur / p.entry_price - 1) * 100
                    ret_color = "#22c55e" if ret >= 0 else "#ef4444"
                    ret_str   = f"{ret:+.1f}%"
                    cur_str   = f"${cur:.2f}"
                except Exception:
                    ret_str   = "—"
                    cur_str   = "—"
                    ret_color = "#8892a4"

                pos_rows += f"""
                <tr>
                  <td style="padding:10px 14px;font-weight:700;">{p.ticker}</td>
                  <td style="padding:10px 14px;">${p.entry_price:.2f}</td>
                  <td style="padding:10px 14px;">{cur_str}</td>
                  <td style="padding:10px 14px;color:{ret_color};font-weight:600;">{ret_str}</td>
                  <td style="padding:10px 14px;color:#8892a4;">{p.score_at_entry:.0f}</td>
                  <td style="padding:10px 14px;color:#8892a4;">{p.entry_date}</td>
                </tr>"""
        else:
            pos_rows = '<tr><td colspan="6" style="padding:16px;text-align:center;color:#8892a4;">No open positions</td></tr>'

        dashboard_btn = ""
        if self.dashboard_url:
            dashboard_btn = f"""
            <div style="text-align:center;margin:24px 0;">
              <a href="{self.dashboard_url}" style="background:#3b82f6;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:600;font-size:0.9rem;display:inline-block;">
                View Live Dashboard →
              </a>
            </div>"""

        html = f"""
        <html><body style="margin:0;padding:0;background:#0f1117;font-family:-apple-system,sans-serif;color:#e2e8f0;">
        <div style="max-width:680px;margin:0 auto;padding:24px;">

          <div style="background:#1a1d27;border:1px solid #2d3150;border-radius:12px;padding:24px;margin-bottom:20px;">
            <h1 style="margin:0 0 4px;font-size:1.3rem;font-weight:800;color:#e2e8f0;">
              🔎 Camillo Morning Scan
            </h1>
            <p style="margin:0;color:#8892a4;font-size:0.82rem;">{now} ET · Portfolio equity: ${account_equity:,.2f}</p>
          </div>

          <div style="background:#1a1d27;border:1px solid #2d3150;border-radius:12px;overflow:hidden;margin-bottom:20px;">
            <div style="padding:14px 18px;border-bottom:1px solid #2d3150;">
              <h2 style="margin:0;font-size:0.8rem;text-transform:uppercase;letter-spacing:0.08em;color:#8892a4;">Watchlist Scores</h2>
            </div>
            <table style="width:100%;border-collapse:collapse;font-size:0.82rem;">
              <thead>
                <tr style="background:#21253a;">
                  <th style="padding:8px 14px;text-align:left;color:#8892a4;font-size:0.7rem;text-transform:uppercase;">Ticker</th>
                  <th style="padding:8px 14px;text-align:left;color:#8892a4;font-size:0.7rem;text-transform:uppercase;">Signal</th>
                  <th style="padding:8px 14px;text-align:left;color:#8892a4;font-size:0.7rem;text-transform:uppercase;">Score</th>
                  <th style="padding:8px 14px;text-align:left;color:#8892a4;font-size:0.7rem;text-transform:uppercase;">Trend</th>
                  <th style="padding:8px 14px;text-align:left;color:#8892a4;font-size:0.7rem;text-transform:uppercase;">Analyst</th>
                  <th style="padding:8px 14px;text-align:left;color:#8892a4;font-size:0.7rem;text-transform:uppercase;">Reddit</th>
                  <th style="padding:8px 14px;text-align:left;color:#8892a4;font-size:0.7rem;text-transform:uppercase;">Lag</th>
                </tr>
              </thead>
              <tbody>{scan_rows}</tbody>
            </table>
          </div>

          <div style="background:#1a1d27;border:1px solid #2d3150;border-radius:12px;overflow:hidden;margin-bottom:20px;">
            <div style="padding:14px 18px;border-bottom:1px solid #2d3150;">
              <h2 style="margin:0;font-size:0.8rem;text-transform:uppercase;letter-spacing:0.08em;color:#8892a4;">Open Positions</h2>
            </div>
            <table style="width:100%;border-collapse:collapse;font-size:0.82rem;">
              <thead>
                <tr style="background:#21253a;">
                  <th style="padding:8px 14px;text-align:left;color:#8892a4;font-size:0.7rem;text-transform:uppercase;">Ticker</th>
                  <th style="padding:8px 14px;text-align:left;color:#8892a4;font-size:0.7rem;text-transform:uppercase;">Entry</th>
                  <th style="padding:8px 14px;text-align:left;color:#8892a4;font-size:0.7rem;text-transform:uppercase;">Current</th>
                  <th style="padding:8px 14px;text-align:left;color:#8892a4;font-size:0.7rem;text-transform:uppercase;">P&L</th>
                  <th style="padding:8px 14px;text-align:left;color:#8892a4;font-size:0.7rem;text-transform:uppercase;">Score</th>
                  <th style="padding:8px 14px;text-align:left;color:#8892a4;font-size:0.7rem;text-transform:uppercase;">Entered</th>
                </tr>
              </thead>
              <tbody>{pos_rows}</tbody>
            </table>
          </div>

          {dashboard_btn}

          <p style="text-align:center;color:#6b7280;font-size:0.7rem;margin-top:16px;">
            Camillo Social Arbitrage Bot · Unsubscribe by removing CAMILLO_EMAIL_FROM env var
          </p>
        </div>
        </body></html>"""

        plain = f"Camillo Morning Scan — {now}\n\n"
        if scan_results is not None and not scan_results.empty:
            for _, row in scan_results.iterrows():
                plain += f"{row.get('Ticker',''):6s}  {row.get('Signal',''):5s}  {row.get('Composite',0):.1f}\n"
        if self.dashboard_url:
            plain += f"\nDashboard: {self.dashboard_url}\n"

        self.send(subject, html, plain)

    def send_daily_summary(self, positions, account_equity: float):
        """End-of-day summary email."""
        now     = datetime.now().strftime("%Y-%m-%d")
        subject = f"Camillo Daily Summary — {now}"

        total_pl   = 0.0
        pos_lines  = []
        for p in positions:
            try:
                from camillo_bot.yahoo_data import get_client
                cur = get_client().get_price(p.ticker)
                ret = (cur / p.entry_price - 1) * 100
                pl  = (cur - p.entry_price) * p.qty
                total_pl += pl
                pos_lines.append(f"{p.ticker:6s}  entry ${p.entry_price:.2f}  now ${cur:.2f}  {ret:+.1f}%  P&L ${pl:+.2f}")
            except Exception:
                pos_lines.append(f"{p.ticker:6s}  entry ${p.entry_price:.2f}  (price unavailable)")

        pl_color  = "#22c55e" if total_pl >= 0 else "#ef4444"
        pl_sign   = "+" if total_pl >= 0 else ""
        n         = len(positions)

        dashboard_btn = ""
        if self.dashboard_url:
            dashboard_btn = f"""
            <div style="text-align:center;margin:20px 0;">
              <a href="{self.dashboard_url}" style="background:#3b82f6;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:600;font-size:0.9rem;display:inline-block;">
                View Live Dashboard →
              </a>
            </div>"""

        rows = "".join(
            f'<tr><td style="padding:10px 14px;font-weight:700;">{ln.split()[0]}</td>'
            f'<td style="padding:10px 14px;color:#8892a4;">' + "  ".join(ln.split()[1:]) + "</td></tr>"
            for ln in pos_lines
        ) or '<tr><td colspan="2" style="padding:16px;text-align:center;color:#8892a4;">No open positions</td></tr>'

        html = f"""
        <html><body style="margin:0;padding:0;background:#0f1117;font-family:-apple-system,sans-serif;color:#e2e8f0;">
        <div style="max-width:600px;margin:0 auto;padding:24px;">
          <div style="background:#1a1d27;border:1px solid #2d3150;border-radius:12px;padding:24px;margin-bottom:20px;">
            <h1 style="margin:0 0 4px;font-size:1.3rem;font-weight:800;">📊 Daily Summary — {now}</h1>
            <p style="margin:8px 0 0;color:#8892a4;font-size:0.85rem;">{n} open position{'s' if n!=1 else ''} · Equity ${account_equity:,.2f}</p>
            <p style="margin:4px 0 0;font-size:1.1rem;font-weight:700;color:{pl_color};">Unrealized P&L: {pl_sign}${abs(total_pl):,.2f}</p>
          </div>
          <div style="background:#1a1d27;border:1px solid #2d3150;border-radius:12px;overflow:hidden;margin-bottom:20px;">
            <table style="width:100%;border-collapse:collapse;font-size:0.82rem;">
              <tbody>{rows}</tbody>
            </table>
          </div>
          {dashboard_btn}
        </div>
        </body></html>"""

        plain = f"Camillo Daily Summary — {now}\n{n} positions · Equity ${account_equity:,.2f}\n\n"
        plain += "\n".join(pos_lines) or "No open positions"
        if self.dashboard_url:
            plain += f"\n\nDashboard: {self.dashboard_url}"

        self.send(subject, html, plain)
