"""German investment tax engine (SIMULATOR_SPEC section 5).

Rules implemented, each one testable in isolation:

- Abgeltungsteuer 25% + 5.5% Soli = 26.375% (church tax optional).
- Loss pots: STOCK losses may offset only STOCK SALE gains
  (Aktien-Topf); general losses (ETF, other) offset any capital income
  including stock gains and dividends. Both carry forward forever.
- Sparer-Pauschbetrag: first 1,000 EUR of capital income per calendar
  year is free (an allowance — only the excess is taxed).
- Equity UCITS ETF: 30% Teilfreistellung on gains AND losses
  -> effective 18.4625%.
- US dividends: 15% withheld at source, credited against German tax.
- Gold (Xetra-Gold) is NOT capital income but a §23 private sale:
  tax-free after 365 days; otherwise taxed at the personal rate, and
  the 1,000 EUR is a Freigrenze (a threshold — exceed it and the WHOLE
  yearly private gain is taxable), settled at year end.
"""

from dataclasses import dataclass, field

STOCK, ETF_EQUITY, GOLD = 'stock', 'etf_equity', 'gold'


@dataclass
class TaxState:
    """Per-book tax memory. Pots carry forward; the year fields reset."""
    pot_stocks: float = 0.0        # Aktienverluste
    pot_general: float = 0.0       # sonstige Verluste
    year: int = 0
    allowance_used: float = 0.0    # Sparer-Pauschbetrag consumed this year
    private_gains: float = 0.0     # §23 net gains this year (gold)
    private_settled_tax: float = 0.0   # §23 tax already charged this year

    def roll_year(self, year: int) -> None:
        """New calendar year: allowance and §23 tally reset, pots stay."""
        if year != self.year:
            self.year = year
            self.allowance_used = 0.0
            self.private_gains = 0.0
            self.private_settled_tax = 0.0


def effective_rate(cfg: dict) -> float:
    t = cfg['tax']
    return t['abgeltungsteuer'] * (1.0 + t['soli'] + t['church_tax'])


def _apply_allowance(state: TaxState, base: float, cfg: dict) -> float:
    """Consume the yearly Sparer-Pauschbetrag; return the taxable rest."""
    left = max(0.0, cfg['tax']['allowance_eur'] - state.allowance_used)
    used = min(left, base)
    state.allowance_used += used
    return base - used


def tax_on_sale(state: TaxState, asset_class: str, gain_eur: float,
                held_days: int, year: int, cfg: dict) -> float:
    """Tax withheld on one realized sale. Mutates `state`.

    Gold returns 0 here by design: §23 tax is accrued in
    `state.private_gains` and settled by `settle_private_sales`."""
    state.roll_year(year)

    if asset_class == GOLD:
        if held_days >= cfg['tax']['private_sale_holding_days']:
            return 0.0
        state.private_gains += gain_eur
        return 0.0

    base = gain_eur
    if asset_class == ETF_EQUITY:
        base *= (1.0 - cfg['tax']['etf_teilfreistellung'])

    if base <= 0:
        if asset_class == STOCK:
            state.pot_stocks += -base
        else:
            state.pot_general += -base
        return 0.0

    if asset_class == STOCK:
        used = min(state.pot_stocks, base)     # Aktien-Topf first ...
        state.pot_stocks -= used
        base -= used
    used = min(state.pot_general, base)        # ... then the general pot
    state.pot_general -= used
    base -= used

    return round(_apply_allowance(state, base, cfg) * effective_rate(cfg), 2)


def tax_on_dividend(state: TaxState, gross_eur: float, year: int,
                    cfg: dict, us_source: bool = True,
                    asset_class: str = STOCK) -> dict:
    """Split one dividend into withholding, German tax and net cash.

    Stock-loss pots may NOT offset dividends; the general pot may."""
    state.roll_year(year)
    t = cfg['tax']
    withheld = round(gross_eur * t['us_withholding'], 2) if us_source else 0.0

    base = gross_eur
    if asset_class == ETF_EQUITY:
        base *= (1.0 - t['etf_teilfreistellung'])
    used = min(state.pot_general, base)
    state.pot_general -= used
    base -= used
    taxable = _apply_allowance(state, base, cfg)

    german = taxable * effective_rate(cfg)
    german = round(max(0.0, german - withheld), 2)   # credit the withholding
    return {'gross_eur': round(gross_eur, 2), 'withheld_eur': withheld,
            'german_tax_eur': german,
            'net_eur': round(gross_eur - withheld - german, 2)}


def settle_private_sales(state: TaxState, year: int, cfg: dict) -> float:
    """§23 settlement for early gold sales (Freigrenze rule).

    Charges only what is not yet charged for `year`, so calling it more
    than once — mid-year, then again after later sales — stays correct
    and never double-taxes."""
    state.roll_year(year)
    gains = state.private_gains
    due = 0.0 if gains < cfg['tax']['private_sale_freigrenze_eur'] \
        else gains * cfg['tax']['personal_rate']
    delta = round(due - state.private_settled_tax, 2)
    state.private_settled_tax = round(due, 2)
    return max(0.0, delta)
