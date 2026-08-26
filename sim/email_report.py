"""Build and send the weekly email (SIMULATOR_SPEC section 1).

Recommendations are split into one section per SOURCE (LPPL_DIP2 and
STEADY_GIANTS) and every row also carries the label in its own column,
so the origin of a suggestion is never ambiguous.
"""

import os
import smtplib
from email.message import EmailMessage

CSS = ('body{font-family:system-ui,Arial,sans-serif;font-size:14px}'
       'table{border-collapse:collapse;margin:6px 0 18px 0;width:100%}'
       'th,td{border:1px solid #ccc;padding:4px 7px;text-align:left}'
       'th{background:#f0f0f0}.blocked{color:#999}.warn{background:#ffe9e9}'
       '.ok{color:#0a7a0a;font-weight:600}.src{font-family:monospace}'
       'h2{margin:18px 0 4px 0;font-size:16px}'
       'footer{color:#777;font-size:12px;margin-top:22px}')


def _rec_table(rows: list[dict]) -> str:
    if not rows:
        return '<p><i>no candidates this week</i></p>'
    out = ['<table><tr><th>Symbol</th><th>Source</th><th>Status</th>'
           '<th>Price</th><th>Detail</th></tr>']
    for r in rows:
        cls = '' if r['buyable'] else ' class="blocked"'
        status = '<span class="ok">BUYABLE</span>' if r['buyable'] \
            else f"BLOCKED — {r['reason']}"
        out.append(f'<tr{cls}><td><b>{r["symbol"]}</b></td>'
                   f'<td class="src">{r["source"]}</td><td>{status}</td>'
                   f'<td>{r["price"]:.2f}</td><td>{r["detail"]}</td></tr>')
    return ''.join(out) + '</table>'


def _warn_table(rows: list[dict]) -> str:
    if not rows:
        return '<p><i>nothing to watch</i></p>'
    out = ['<table><tr><th>Symbol</th><th>Bubble state</th><th>Votes</th>'
           '<th>Est. critical time</th><th>Price</th></tr>']
    for r in rows:
        cls = ' class="warn"' if r['warn'] else ''
        out.append(f'<tr{cls}><td><b>{r["symbol"]}</b></td>'
                   f'<td>{r["level"]}</td><td>{r["votes"]}/5</td>'
                   f'<td>{r["tc_date"] or "-"}</td>'
                   f'<td>{r["price"]:.2f}</td></tr>')
    return ''.join(out) + '</table>'


def _books_table(books: list[dict]) -> str:
    if not books:
        return '<p><i>no books yet</i></p>'
    out = ['<table><tr><th>Book</th><th>Equity (EUR)</th><th>Cash (EUR)</th>'
           '<th>Positions</th></tr>']
    for b in books:
        out.append(f'<tr><td>{b["name"]}</td><td>{b["equity"]:,.2f}</td>'
                   f'<td>{b["cash"]:,.2f}</td><td>{b["positions"]}</td></tr>')
    return ''.join(out) + '</table>'


def build_email(asof: str, light: dict, recs: list[dict],
                warns: list[dict], books: list[dict]) -> tuple[str, str, str]:
    dip = [r for r in recs if r['source'] == 'LPPL_DIP2']
    giants = [r for r in recs if r['source'] == 'STEADY_GIANTS']
    n_buy = sum(1 for r in recs if r['buyable'])
    n_warn = sum(1 for w in warns if w['warn'])
    subject = (f'Trading simulator {asof} — {n_buy} buyable, '
               f'{n_warn} bubble warning(s)')

    lamp = 'GREEN — buying allowed' if light['green'] else (
        'RED — no new entries ('
        + ('trend down' if not light['trend'] else 'volatility spike') + ')')
    html = (f'<html><head><style>{CSS}</style></head><body>'
            f'<h1>Weekly report — {asof}</h1>'
            f'<p><b>Market light:</b> {lamp} '
            f'(SPY {light["spy"]:.2f}, 200d SMA {light["sma200"]:.2f})</p>'
            f'<h2>Your books</h2>{_books_table(books)}'
            f'<h2>1a. Recommended to buy — LPPL_DIP2 (bubble dip-buyer)</h2>'
            f'{_rec_table(dip)}'
            f'<h2>1b. Recommended to buy — STEADY_GIANTS (compounders)</h2>'
            f'{_rec_table(giants)}'
            f'<h2>2. Bubble warnings — your holdings, gold, S&amp;P 500</h2>'
            f'{_warn_table(warns)}'
            '<footer>Only BUYABLE names can be bought in the simulator; '
            'gold and the S&amp;P 500 ETF are always available. Orders you '
            'place execute at the NEXT trading day&rsquo;s opening price. '
            'Signals inherit every research caveat (survivorship-biased '
            'universe, unproven edge — see FINDINGS.md): this system proves '
            'process, not profit.</footer></body></html>')

    lines = [f'Weekly report — {asof}', f'Market light: {lamp}', '']
    for title, rows in (('LPPL_DIP2 recommendations', dip),
                        ('STEADY_GIANTS recommendations', giants)):
        lines.append(title)
        lines += [f'  [{r["source"]}] {r["symbol"]} {r["price"]:.2f} '
                  f'{"BUYABLE" if r["buyable"] else "BLOCKED — " + r["reason"]}'
                  f' | {r["detail"]}' for r in rows] or ['  (none)']
        lines.append('')
    lines.append('Bubble warnings')
    lines += [f'  {w["symbol"]}: {w["level"]}, {w["votes"]}/5 votes, '
              f'tc ~{w["tc_date"] or "-"}' for w in warns] or ['  (none)']
    return subject, '\n'.join(lines), html


def send_email(subject: str, text: str, html: str, to_addr: str,
               sim_cfg: dict) -> bool:
    """Send via SMTP. Returns False (without raising) when disabled or
    unconfigured, so the weekly job still completes and logs the report."""
    e = sim_cfg['email']
    if not (e.get('enabled') and e.get('smtp_host') and to_addr):
        return False
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = e.get('from_addr') or e['smtp_user']
    msg['To'] = to_addr
    msg.set_content(text)
    msg.add_alternative(html, subtype='html')
    password = os.environ.get(e.get('smtp_password_env', ''), '')
    with smtplib.SMTP(e['smtp_host'], int(e.get('smtp_port', 587))) as s:
        s.starttls()
        if e.get('smtp_user'):
            s.login(e['smtp_user'], password)
        s.send_message(msg)
    return True
