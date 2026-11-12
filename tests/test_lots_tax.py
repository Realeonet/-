"""Лоты и израильский налог: все ожидаемые числа посчитаны вручную."""
from datetime import date
from decimal import Decimal as D

import pytest

from app.domain.lots import (
    InsufficientQuantity,
    LotState,
    cost_of,
    fifo_allocate,
    manual_allocate,
    split_for_transfer,
    unit_cost,
)
from app.domain.tax_il import (
    annual_summary,
    compute_gain,
    dividend_tax,
    effective_rate,
    net_of_tax,
    rank_lots_for_sale,
    simulate_sale,
)


def lot(id=1, qty="10", remaining=None, price="100", fees="10", fx="3.5", day=date(2024, 1, 10), ccy="USD"):
    return LotState(id=id, buy_date=day, qty=D(qty), remaining=D(remaining or qty), price=D(price),
                    fees=D(fees), currency=ccy, fx_ccy_ils=D(fx), fx_usd_ils=D(fx))


def test_cost_includes_proportional_fees():
    l = lot()
    assert cost_of(l, D(4)) == D("404")          # 4*100 + 10*4/10
    assert cost_of(l, D(10)) == D("1010")
    assert unit_cost(l) == D("101")


def test_fifo_takes_oldest_first_and_splits():
    a = lot(id=1, qty="5", day=date(2024, 3, 1))
    b = lot(id=2, qty="5", day=date(2023, 3, 1))
    c = lot(id=3, qty="5", day=date(2025, 3, 1))
    alloc = fifo_allocate([a, b, c], D(7))
    assert [(l.id, q) for l, q in alloc] == [(2, D(5)), (1, D(2))]


def test_fifo_skips_empty_and_raises_when_short():
    a = lot(id=1, qty="5", remaining="0")
    b = lot(id=2, qty="5", remaining="3")
    assert [(l.id, q) for l, q in fifo_allocate([a, b], D(3))] == [(2, D(3))]
    with pytest.raises(InsufficientQuantity):
        fifo_allocate([a, b], D("3.00000001"))


def test_manual_allocate_validates_remaining():
    a, b = lot(id=1, qty="5"), lot(id=2, qty="5", remaining="1")
    assert [(l.id, q) for l, q in manual_allocate([a, b], {2: D(1), 1: D(0)})] == [(2, D(1))]
    with pytest.raises(InsufficientQuantity):
        manual_allocate([a, b], {2: D(2)})
    with pytest.raises(ValueError):
        manual_allocate([a, b], {99: D(1)})


def test_gain_nominal_ils_includes_fx_difference():
    # 4 шт: себестоимость $404 по 3.5 = ₪1414; выручка $598 по 3.0 = ₪1794
    g = compute_gain(lot(), D(4), D(150), D(2), "USD", D("3.0"), D("3.0"), D("0.25"), "nominal")
    assert g.cost_ccy == D("404") and g.proceeds_ccy == D("598")
    assert g.gain_usd == D("194")
    assert g.cost_ils == D("1414.0") and g.proceeds_ils == D("1794.0")
    assert g.gain_ils_nominal == D("380.0")
    assert g.taxable_ils == D("380.0")
    assert g.tax_ils == D("95.000")


def test_gain_linked_method_taxes_currency_gain_at_sale_rate():
    g = compute_gain(lot(), D(4), D(150), D(2), "USD", D("3.0"), D("3.0"), D("0.25"), "linked")
    assert g.gain_ils_linked == D("582.0")       # 194 * 3.0
    assert g.tax_ils == D("145.500")
    # шекель ослаб: номинальный метод даёт больше налога, чем привязанный
    weak_nom = compute_gain(lot(), D(4), D(150), D(2), "USD", D("4.0"), D("4.0"), D("0.25"), "nominal")
    weak_lnk = compute_gain(lot(), D(4), D(150), D(2), "USD", D("4.0"), D("4.0"), D("0.25"), "linked")
    assert weak_nom.taxable_ils == D("978.0")    # 2392 − 1414
    assert weak_lnk.taxable_ils == D("776.0")    # 194 * 4


def test_usd_profit_but_ils_loss_means_no_tax_under_nominal():
    # +$46 в долларах, но шекель укрепился 3.5 → 3.0: в шекелях убыток
    g = compute_gain(lot(fees="0"), D(4), D("111.5"), D(0), "USD", D("3.0"), D("3.0"))
    assert g.gain_usd == D("46.0")
    assert g.gain_ils_nominal == D("-62.00")     # 446*3 − 400*3.5
    assert g.tax_ils == 0


def test_loss_has_zero_tax_and_negative_taxable():
    g = compute_gain(lot(), D(4), D(90), D(0), "USD", D("3.5"), D("3.5"))
    assert g.taxable_ils == D("-154.0")
    assert g.tax_ils == 0


def test_ils_asset_needs_no_fx():
    l = LotState(1, date(2024, 1, 1), D(100), D(100), D(50), D(0), "ILS", D(1), D("3.6"))
    g = compute_gain(l, D(100), D(60), D(10), "ILS", D(1), D("3.2"), D("0.25"), "linked")
    assert g.gain_ils_nominal == D("990") == g.gain_ils_linked
    assert g.tax_ils == D("247.50")


def test_gain_rejects_bad_input():
    with pytest.raises(ValueError):
        compute_gain(lot(), D(11), D(1), D(0), "USD", D(3), D(3))
    with pytest.raises(ValueError):
        compute_gain(lot(), D(1), D(1), D(0), "USD", D(0), D(3))
    with pytest.raises(ValueError):
        compute_gain(lot(), D(1), D(1), D(0), "USD", D(3), D(3), method="magic")


def test_simulate_sale_offsets_loss_lot_against_gain_lot():
    winner = lot(id=1, qty="10", price="100", fees="0", fx="3.0")
    loser = lot(id=2, qty="10", price="200", fees="0", fx="3.0")
    sim = simulate_sale([(winner, D(10)), (loser, D(10))], D(150), D(0), "USD", D("3.0"), D("3.0"))
    # +500$ и −500$ → ноль налога
    assert sim.taxable_ils == 0 and sim.tax_ils == 0
    assert sim.proceeds_usd == D("3000") and sim.net_ils == D("9000.0")


def test_simulate_sale_splits_fee_by_quantity():
    a, b = lot(id=1, qty="3", fees="0"), lot(id=2, qty="1", fees="0")
    sim = simulate_sale([(a, D(3)), (b, D(1))], D(100), D(8), "USD", D("3.5"), D("3.5"))
    assert [g.proceeds_ccy for g in sim.gains] == [D("294"), D("98")]
    assert sim.tax_ils == 0  # продали ниже себестоимости с учётом комиссии


def test_rank_puts_losses_and_small_gains_first():
    cheap = lot(id=1, price="50", fees="0", fx="3.0")
    mid = lot(id=2, price="140", fees="0", fx="3.0")
    dear = lot(id=3, price="200", fees="0", fx="3.0")
    ranked = rank_lots_for_sale([cheap, mid, dear], D(150), "USD", D("3.0"), D("3.0"))
    assert [l.id for l, _ in ranked] == [3, 2, 1]


def test_transfer_split_preserves_total_cost_and_purchase_price():
    btc = lot(qty="1", price="30000", fees="30", fx="3.7")
    s = split_for_transfer(btc, D("0.5"), D("0.4995"))
    assert s.source_qty == D("0.5") and s.source_remaining == D("0.5") and s.source_fees == D("15")
    assert s.dest_price == D("30000") and s.dest_qty == D("0.4995")
    assert s.dest_fees == D("30.0")              # 15015 − 0.4995*30000
    # полная себестоимость сохранена: источник 15015 + получатель 15015 = 30030
    assert s.source_qty * btc.price + s.source_fees + s.dest_qty * s.dest_price + s.dest_fees == D("30030")


def test_transfer_split_adds_money_fee_and_validates():
    l = lot(qty="10", price="100", fees="0")
    s = split_for_transfer(l, D(10), extra_fee_ccy=D(25))
    assert s.dest_fees == D(25) and s.source_qty == 0
    with pytest.raises(InsufficientQuantity):
        split_for_transfer(l, D(11))
    with pytest.raises(ValueError):
        split_for_transfer(l, D(5), D(6))


def test_dividend_tax_credit_cases():
    assert dividend_tax(D(100), D(25)).to_pay == 0            # США удержали 25% — доплаты нет
    assert dividend_tax(D(100), D(25)).net == D(75)
    assert dividend_tax(D(100), D(0)).to_pay == D("25.00")    # UCITS без удержания — 25% декларировать
    assert dividend_tax(D(100), D(15)).to_pay == D("10.00")
    over = dividend_tax(D(100), D(30))                        # удержали больше 25% — излишек не зачитывается
    assert over.to_pay == 0 and over.net == D(70) and over.foreign_credit == D("25.00")
    assert dividend_tax(D(100), D(0), D(25)).to_pay == 0      # израильский брокер удержал сам
    assert net_of_tax(D(200)) == D("150.00")
    with pytest.raises(ValueError):
        dividend_tax(D(-1))


def test_annual_summary_offsets_losses_and_counts_withheld():
    s = annual_summary([(D(1000), D(0)), (D(-400), D(0)), (D(200), D(50))])
    assert s.gains_ils == D(1200) and s.losses_ils == D(400) and s.net_ils == D(800)
    assert s.tax_ils == D("200.00") and s.withheld_ils == D(50) and s.to_declare_ils == D("150.00")
    loss_year = annual_summary([(D(-300), D(0)), (D(100), D(0))])
    assert loss_year.tax_ils == 0 and loss_year.loss_carry_ils == D(200)


def test_effective_rate():
    assert effective_rate(D(25)) == D("0.25")
    assert effective_rate(D(25), D(3)) == D("0.28")
    with pytest.raises(ValueError):
        effective_rate(D(99), D(2))
