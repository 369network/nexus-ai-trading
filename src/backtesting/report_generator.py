"""
NEXUS ALPHA — Backtest Report Generator
=========================================
Generates interactive HTML reports (Plotly) and PDF exports from
BacktestResult objects. Saves key metrics to Supabase.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import structlog

from src.backtesting.engine import BacktestResult, Trade

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# HTML Report
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>NEXUS ALPHA — Backtest Report: {symbol}</title>
<script src="https://cdn.plot.ly/plotly-2.26.0.min.js"></script>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background:#0f1117; color:#e0e0e0; margin:0; padding:20px; }}
  h1 {{ color:#00d4aa; border-bottom:1px solid #333; padding-bottom:8px; }}
  h2 {{ color:#7fb3f5; margin-top:32px; }}
  .metrics-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:12px; margin:16px 0; }}
  .metric-card {{ background:#1a1d27; border-radius:8px; padding:14px 18px; border-left:3px solid #00d4aa; }}
  .metric-label {{ font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.5px; }}
  .metric-value {{ font-size:22px; font-weight:600; color:#ffffff; margin-top:4px; }}
  .metric-value.positive {{ color:#00d4aa; }}
  .metric-value.negative {{ color:#ff4d6d; }}
  .chart-container {{ background:#1a1d27; border-radius:8px; padding:16px; margin:16px 0; }}
  table {{ width:100%; border-collapse:collapse; background:#1a1d27; border-radius:8px; overflow:hidden; }}
  th {{ background:#252836; padding:10px 14px; text-align:left; font-size:12px; color:#888; text-transform:uppercase; }}
  td {{ padding:9px 14px; border-bottom:1px solid #252836; font-size:13px; }}
  tr:hover td {{ background:#252836; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }}
  .badge-win {{ background:#0d3d2e; color:#00d4aa; }}
  .badge-loss {{ background:#3d0d1a; color:#ff4d6d; }}
  .footer {{ text-align:center; color:#555; margin-top:40px; font-size:12px; }}
</style>
</head>
<body>
<h1>NEXUS ALPHA Backtest Report</h1>
<p style="color:#888">Symbol: <strong style="color:#fff">{symbol}</strong> &nbsp;|&nbsp;
Period: <strong style="color:#fff">{start_date}</strong> → <strong style="color:#fff">{end_date}</strong> &nbsp;|&nbsp;
Generated: <strong style="color:#fff">{generated_at}</strong></p>

<h2>Key Metrics</h2>
<div class="metrics-grid">
{metric_cards}
</div>

<h2>Equity Curve</h2>
<div class="chart-container">
<div id="equity_chart"></div>
</div>

<h2>Drawdown</h2>
<div class="chart-container">
<div id="drawdown_chart"></div>
</div>

<h2>Monthly Returns Heatmap</h2>
<div class="chart-container">
<div id="monthly_heatmap"></div>
</div>

<h2>Trade P&amp;L Distribution</h2>
<div class="chart-container">
<div id="pnl_dist_chart"></div>
</div>

<h2>Trade Log ({n_trades} trades)</h2>
<table>
<thead>
<tr>
  <th>#</th><th>Symbol</th><th>Direction</th><th>Entry Time</th><th>Exit Time</th>
  <th>Entry Price</th><th>Exit Price</th><th>Size</th><th>P&amp;L</th>
  <th>Commission</th><th>Exit Reason</th>
</tr>
</thead>
<tbody>
{trade_rows}
</tbody>
</table>

<script>
{plotly_scripts}
</script>

<div class="footer">NEXUS ALPHA Trading System &copy; 2024. For internal use only.</div>
</body>
</html>
"""


def generate_html_report(result: BacktestResult, output_path: str) -> str:
    """
    Generate a fully self-contained HTML backtest report.

    Includes:
    - Key metrics grid (Sharpe, Sortino, drawdown, win rate, etc.)
    - Interactive Plotly equity curve with buy/sell markers.
    - Drawdown chart.
    - Monthly returns heatmap.
    - Trade P&L distribution histogram.
    - Full trade log table.

    Args:
        result: BacktestResult from BacktestEngine.run().
        output_path: Path to write the HTML file (e.g. "reports/btc_backtest.html").

    Returns:
        Absolute path to the generated HTML file.
    """
    log.info("report_generator_html_start", symbol=result.symbol, output=output_path)

    # --- Metric cards ---
    metric_cards = _build_metric_cards(result.metrics, result.initial_capital, result.final_equity)

    # --- Charts ---
    plotly_scripts = _build_plotly_scripts(result)

    # --- Trade rows ---
    trade_rows = _build_trade_rows(result.trades)

    html = _HTML_TEMPLATE.format(
        symbol=result.symbol,
        start_date=result.start_date.strftime("%Y-%m-%d"),
        end_date=result.end_date.strftime("%Y-%m-%d"),
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        metric_cards=metric_cards,
        plotly_scripts=plotly_scripts,
        trade_rows=trade_rows,
        n_trades=len([t for t in result.trades if not t.is_open]),
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    log.info("report_generator_html_done", path=str(out.resolve()))
    return str(out.resolve())


def generate_pdf_report(result: BacktestResult, output_path: str) -> Optional[str]:
    """
    Generate a PDF report from the HTML report using weasyprint or pdfkit.

    Attempts weasyprint first (better CSS support), falls back to pdfkit.
    Returns the PDF path on success, or None if no PDF library is available.

    Args:
        result: BacktestResult.
        output_path: Output path for the PDF (e.g. "reports/report.pdf").

    Returns:
        Path to PDF file, or None.
    """
    # First generate HTML
    html_path = output_path.replace(".pdf", ".html")
    generate_html_report(result, html_path)

    html_content = Path(html_path).read_text(encoding="utf-8")

    # Try weasyprint
    try:
        from weasyprint import HTML as WeasyprintHTML  # type: ignore

        pdf_out = Path(output_path)
        pdf_out.parent.mkdir(parents=True, exist_ok=True)
        WeasyprintHTML(string=html_content).write_pdf(str(pdf_out))
        log.info("report_generator_pdf_weasyprint", path=str(pdf_out.resolve()))
        return str(pdf_out.resolve())
    except ImportError:
        log.debug("report_generator_weasyprint_not_installed")
    except Exception as exc:
        log.warning("report_generator_weasyprint_error", error=str(exc))

    # Try pdfkit
    try:
        import pdfkit  # type: ignore

        pdf_out = Path(output_path)
        pdf_out.parent.mkdir(parents=True, exist_ok=True)
        pdfkit.from_string(html_content, str(pdf_out), options={"quiet": ""})
        log.info("report_generator_pdf_pdfkit", path=str(pdf_out.resolve()))
        return str(pdf_out.resolve())
    except ImportError:
        log.debug("report_generator_pdfkit_not_installed")
    except Exception as exc:
        log.warning("report_generator_pdfkit_error", error=str(exc))

    log.warning("report_generator_pdf_unavailable", note="Install weasyprint or pdfkit")
    return None


async def save_metrics_to_supabase(result: BacktestResult, strategy_name: str = "") -> bool:
    """
    Persist backtest metrics to the Supabase ``backtest_results`` table.

    Args:
        result: BacktestResult with computed metrics.
        strategy_name: Optional strategy identifier.

    Returns:
        True if stored successfully, False otherwise.
    """
    try:
        from src.db.supabase_client import SupabaseClient
        from src.config import get_settings

        settings = get_settings()
        client = await SupabaseClient.get_instance(
            url=settings.supabase_url,
            key=settings.supabase_service_key,
        )

        row = {
            "symbol": result.symbol,
            "strategy": strategy_name or "unknown",
            "start_date": result.start_date.isoformat(),
            "end_date": result.end_date.isoformat(),
            "initial_capital": result.initial_capital,
            "final_equity": result.final_equity,
            "total_return_pct": result.metrics.get("total_return_pct"),
            "cagr_pct": result.metrics.get("cagr_pct"),
            "sharpe_ratio": result.metrics.get("sharpe_ratio"),
            "sortino_ratio": result.metrics.get("sortino_ratio"),
            "calmar_ratio": result.metrics.get("calmar_ratio"),
            "max_drawdown_pct": result.metrics.get("max_drawdown_pct"),
            "win_rate_pct": result.metrics.get("win_rate_pct"),
            "profit_factor": result.metrics.get("profit_factor"),
            "total_trades": result.metrics.get("total_trades"),
            "expectancy": result.metrics.get("expectancy"),
            "metrics_json": json.dumps(result.metrics),
        }

        await asyncio.to_thread(
            lambda: client._client.table("backtest_results").upsert(row).execute()  # type: ignore[union-attr]
        )
        log.info("report_generator_supabase_saved", symbol=result.symbol)
        return True

    except Exception as exc:
        log.warning("report_generator_supabase_failed", error=str(exc))
        return False


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_metric_cards(
    metrics: Dict[str, Any],
    initial_capital: float,
    final_equity: float,
) -> str:
    """Build HTML for the key metrics grid."""

    def card(label: str, value: str, is_positive: Optional[bool] = None) -> str:
        css = ""
        if is_positive is True:
            css = "positive"
        elif is_positive is False:
            css = "negative"
        return (
            f'<div class="metric-card">'
            f'<div class="metric-label">{label}</div>'
            f'<div class="metric-value {css}">{value}</div>'
            f"</div>"
        )

    def fmt_pct(v: Optional[float]) -> str:
        return f"{v:.2f}%" if v is not None else "N/A"

    def fmt_ratio(v: Optional[float]) -> str:
        return f"{v:.3f}" if v is not None else "N/A"

    ret = metrics.get("total_return_pct", 0.0)
    dd = metrics.get("max_drawdown_pct", 0.0)

    cards = [
        card("Total Return", fmt_pct(ret), ret > 0 if ret is not None else None),
        card("CAGR", fmt_pct(metrics.get("cagr_pct"))),
        card("Sharpe Ratio", fmt_ratio(metrics.get("sharpe_ratio")),
             (metrics.get("sharpe_ratio") or 0) > 1),
        card("Sortino Ratio", fmt_ratio(metrics.get("sortino_ratio"))),
        card("Calmar Ratio", fmt_ratio(metrics.get("calmar_ratio"))),
        card("Max Drawdown", fmt_pct(dd), dd < 10 if dd is not None else None),
        card("Win Rate", fmt_pct(metrics.get("win_rate_pct"))),
        card("Profit Factor", fmt_ratio(metrics.get("profit_factor"))),
        card("Total Trades", str(int(metrics.get("total_trades", 0)))),
        card("Expectancy", f"${metrics.get('expectancy', 0):.2f}"),
        card("Avg Trade", f"${metrics.get('avg_trade_pnl', 0):.2f}"),
        card("Final Equity", f"${final_equity:,.0f}"),
    ]
    return "\n".join(cards)


def _build_plotly_scripts(result: BacktestResult) -> str:
    """Build Plotly chart initialization scripts."""
    scripts: List[str] = []

    # --- Equity curve ---
    if not result.equity_curve.empty:
        dates = [str(d) for d in result.equity_curve.index]
        equity = result.equity_curve.tolist()

        # Buy/sell markers from trades
        buy_times = [str(t.entry_time) for t in result.trades if t.direction == "LONG"]
        buy_prices = [t.entry_price for t in result.trades if t.direction == "LONG"]
        sell_times = [str(t.exit_time) for t in result.trades if t.exit_time and t.direction == "LONG"]
        sell_prices = [t.exit_price for t in result.trades if t.exit_time and t.direction == "LONG"]

        scripts.append(f"""
Plotly.newPlot('equity_chart', [
  {{
    x: {json.dumps(dates)},
    y: {json.dumps(equity)},
    type: 'scatter',
    mode: 'lines',
    name: 'Portfolio Equity',
    line: {{color: '#00d4aa', width: 2}},
    fill: 'tozeroy',
    fillcolor: 'rgba(0,212,170,0.08)'
  }},
  {{
    x: {json.dumps(buy_times)},
    y: {json.dumps(buy_prices)},
    type: 'scatter',
    mode: 'markers',
    name: 'Buy',
    marker: {{color: '#00d4aa', size: 8, symbol: 'triangle-up'}}
  }},
  {{
    x: {json.dumps(sell_times)},
    y: {json.dumps(sell_prices)},
    type: 'scatter',
    mode: 'markers',
    name: 'Sell',
    marker: {{color: '#ff4d6d', size: 8, symbol: 'triangle-down'}}
  }}
], {{
  paper_bgcolor: '#1a1d27',
  plot_bgcolor: '#1a1d27',
  font: {{color: '#e0e0e0'}},
  xaxis: {{gridcolor: '#252836', title: 'Date'}},
  yaxis: {{gridcolor: '#252836', title: 'Equity ($)', tickprefix: '$'}},
  showlegend: true,
  legend: {{bgcolor: '#252836'}},
  margin: {{t: 20, r: 20, b: 50, l: 80}}
}});
""")

    # --- Drawdown chart ---
    if not result.drawdown_series.empty:
        dd_dates = [str(d) for d in result.drawdown_series.index]
        dd_vals = [v * 100 for v in result.drawdown_series.tolist()]

        scripts.append(f"""
Plotly.newPlot('drawdown_chart', [{{
  x: {json.dumps(dd_dates)},
  y: {json.dumps(dd_vals)},
  type: 'scatter',
  mode: 'lines',
  name: 'Drawdown %',
  line: {{color: '#ff4d6d', width: 1.5}},
  fill: 'tozeroy',
  fillcolor: 'rgba(255,77,109,0.15)'
}}], {{
  paper_bgcolor: '#1a1d27',
  plot_bgcolor: '#1a1d27',
  font: {{color: '#e0e0e0'}},
  xaxis: {{gridcolor: '#252836', title: 'Date'}},
  yaxis: {{gridcolor: '#252836', title: 'Drawdown (%)', ticksuffix: '%'}},
  showlegend: false,
  margin: {{t: 20, r: 20, b: 50, l: 80}}
}});
""")

    # --- Monthly returns heatmap ---
    if not result.equity_curve.empty:
        monthly_script = _build_monthly_heatmap_script(result.equity_curve)
        scripts.append(monthly_script)

    # --- P&L distribution ---
    if result.trades:
        closed = [t for t in result.trades if not t.is_open]
        pnls = [t.realized_pnl for t in closed]
        scripts.append(f"""
Plotly.newPlot('pnl_dist_chart', [{{
  x: {json.dumps(pnls)},
  type: 'histogram',
  nbinsx: 30,
  marker: {{
    color: {json.dumps(['#00d4aa' if p > 0 else '#ff4d6d' for p in pnls])},
    line: {{color: '#1a1d27', width: 0.5}}
  }},
  name: 'Trade P&L'
}}], {{
  paper_bgcolor: '#1a1d27',
  plot_bgcolor: '#1a1d27',
  font: {{color: '#e0e0e0'}},
  xaxis: {{gridcolor: '#252836', title: 'P&L ($)', tickprefix: '$'}},
  yaxis: {{gridcolor: '#252836', title: 'Count'}},
  showlegend: false,
  margin: {{t: 20, r: 20, b: 50, l: 80}}
}});
""")

    return "\n".join(scripts)


def _build_monthly_heatmap_script(equity_curve: pd.Series) -> str:
    """Build Plotly script for a monthly returns heatmap."""
    try:
        monthly = equity_curve.resample("ME").last().pct_change().dropna() * 100
        monthly.index = pd.to_datetime(monthly.index)

        years = sorted(monthly.index.year.unique())
        months = list(range(1, 13))
        month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

        z_matrix: List[List[Optional[float]]] = []
        for yr in years:
            row = []
            for mo in months:
                mask = (monthly.index.year == yr) & (monthly.index.month == mo)
                val = monthly[mask].values
                row.append(round(float(val[0]), 2) if len(val) > 0 else None)
            z_matrix.append(row)

        return f"""
Plotly.newPlot('monthly_heatmap', [{{
  z: {json.dumps(z_matrix)},
  x: {json.dumps(month_names)},
  y: {json.dumps([str(y) for y in years])},
  type: 'heatmap',
  colorscale: [
    [0, '#7b0025'], [0.5, '#1a1d27'], [1, '#00d4aa']
  ],
  zmid: 0,
  colorbar: {{ticksuffix: '%', title: 'Return %'}},
  text: {json.dumps([[f"{v:.1f}%" if v is not None else "" for v in row] for row in z_matrix])},
  texttemplate: '%{{text}}',
  hoverongaps: false
}}], {{
  paper_bgcolor: '#1a1d27',
  plot_bgcolor: '#1a1d27',
  font: {{color: '#e0e0e0'}},
  xaxis: {{title: 'Month'}},
  yaxis: {{title: 'Year', type: 'category'}},
  margin: {{t: 20, r: 80, b: 50, l: 60}}
}});
"""
    except Exception as exc:
        log.debug("report_monthly_heatmap_error", error=str(exc))
        return "document.getElementById('monthly_heatmap').innerHTML = '<p style=\"color:#888\">Monthly data unavailable</p>';"


def _build_trade_rows(trades: List[Trade]) -> str:
    """Build HTML table rows for the trade log."""
    rows: List[str] = []
    closed = [t for t in trades if not t.is_open]
    for i, t in enumerate(closed, 1):
        pnl = t.realized_pnl
        pnl_class = "badge-win" if pnl > 0 else "badge-loss"
        pnl_str = f"+${pnl:,.2f}" if pnl > 0 else f"-${abs(pnl):,.2f}"
        rows.append(
            f"<tr>"
            f"<td>{i}</td>"
            f"<td>{t.symbol}</td>"
            f"<td>{t.direction}</td>"
            f"<td>{t.entry_time.strftime('%Y-%m-%d %H:%M') if t.entry_time else ''}</td>"
            f"<td>{t.exit_time.strftime('%Y-%m-%d %H:%M') if t.exit_time else ''}</td>"
            f"<td>${t.entry_price:,.4f}</td>"
            f"<td>${t.exit_price:,.4f if t.exit_price else 0:,.4f}</td>"
            f"<td>{t.size:,.4f}</td>"
            f"<td><span class='badge {pnl_class}'>{pnl_str}</span></td>"
            f"<td>${t.commission:,.2f}</td>"
            f"<td>{t.exit_reason}</td>"
            f"</tr>"
        )
    return "\n".join(rows) if rows else "<tr><td colspan='11' style='text-align:center;color:#888'>No closed trades</td></tr>"
