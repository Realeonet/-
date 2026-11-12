"""Модели SQLite (SQLAlchemy 2.x).

Деньги, количества и курсы хранятся как текст Decimal — без потери точности.
Схема расширяется аддитивно: при запуске `init_db` создаёт недостающие таблицы и колонки,
поэтому обновление приложения не требует ручных миграций и не трогает данные.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.domain.pension import CLASSES as PENSION_CLASSES

ZERO = Decimal("0")


class Dec(TypeDecorator):
    """Decimal, хранится как текст."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        return None if value is None else format(Decimal(value), "f")

    def process_result_value(self, value, dialect):  # noqa: ANN001
        return None if value is None else Decimal(value)


class Base(DeclarativeBase):
    pass


# ---------- справочники ----------

CUSTODIAN_KINDS: dict[str, str] = {
    "broker": "Брокер",
    "bank": "Банк",
    "exchange": "Криптобиржа / кастодиальная площадка",
    "self_custody": "Self-custody (свой кошелёк)",
    "physical": "Физическое хранение",
    "institution": "Пенсионная / страховая компания",
    "other": "Другое",
}

ASSET_CLASSES: dict[str, str] = {
    "stock": "Акции",
    "etf": "ETF / фонды",
    "crypto": "Криптовалюта",
    "metal": "Физические металлы",
    "cash": "Наличные / валюта",
    "other": "Другое (депозиты и пр.)",
}

PRICE_SOURCES: dict[str, str] = {
    "yfinance": "Yahoo Finance (тикер)",
    "coingecko": "CoinGecko (id монеты)",
    "metal": "Спот металла (XAU, XAG, HG…)",
    "cash": "Наличные (цена = 1)",
    "manual": "Ручной ввод цены",
}


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text())


class Owner(Base):
    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)


class Custodian(Base):
    """Место хранения: брокер, банк, биржа, кошелёк, сейф и т.д."""

    __tablename__ = "custodians"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))
    owner_id: Mapped[int] = mapped_column(ForeignKey("owners.id"))
    country: Mapped[str] = mapped_column(String(64), default="")
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    # комиссии
    fee_trade: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)  # фикс. за сделку
    fee_trade_pct: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)  # % от суммы сделки
    fee_trade_min: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)  # минимум за сделку
    free_trades_per_month: Mapped[int] = mapped_column(Integer(), default=0)
    fee_transfer: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)  # перевод / вывод
    fee_custody_pct: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)  # % в год за хранение
    fee_currency: Mapped[str] = mapped_column(String(8), default="USD")
    # налоги: израильский брокер/банк удерживает налог сам
    withholds_il_tax: Mapped[bool] = mapped_column(Boolean(), default=False)
    # self-custody / физическое
    wallet_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    network: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    watch_address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # только публичный адрес
    location_note: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)  # где физически лежит
    note: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean(), default=False)

    owner = relationship("Owner")

    @property
    def is_self_custody(self) -> bool:
        return self.kind in ("self_custody", "physical")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    asset_class: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    domicile: Mapped[str] = mapped_column(String(8), default="")  # US / IE / IL / LU …
    dist_type: Mapped[str] = mapped_column(String(8), default="na")  # acc / dist / na
    dist_frequency: Mapped[Optional[int]] = mapped_column(Integer(), nullable=True)  # выплат в год
    expense_ratio_pct: Mapped[Optional[Decimal]] = mapped_column(Dec(), nullable=True)
    price_source: Mapped[str] = mapped_column(String(16), default="manual")
    source_id: Mapped[str] = mapped_column(String(64), default="")
    history_ticker: Mapped[str] = mapped_column(String(32), default="")  # тикер Yahoo для длинной истории
    manual_price: Mapped[Optional[Decimal]] = mapped_column(Dec(), nullable=True)
    manual_price_date: Mapped[Optional[date]] = mapped_column(Date(), nullable=True)
    valuation_adj_pct: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)  # премия(+)/дисконт(−) к споту
    us_situs_override: Mapped[Optional[bool]] = mapped_column(Boolean(), nullable=True)
    manual_yield_pct: Mapped[Optional[Decimal]] = mapped_column(Dec(), nullable=True)
    next_ex_date: Mapped[Optional[date]] = mapped_column(Date(), nullable=True)
    next_pay_date: Mapped[Optional[date]] = mapped_column(Date(), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)

    @property
    def us_situs(self) -> bool:
        """US-situs для налога на наследство: акции и ETF с домицилем США."""
        if self.us_situs_override is not None:
            return self.us_situs_override
        return self.asset_class in ("stock", "etf") and (self.domicile or "").upper() == "US"

    @property
    def price_key(self) -> str:
        return f"{self.price_source}:{self.source_id}"


class Lot(Base):
    """Лот: отдельная покупка. Инвариант: qty = qty_remaining + Σ продаж по лоту."""

    __tablename__ = "lots"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    custodian_id: Mapped[int] = mapped_column(ForeignKey("custodians.id"))
    buy_date: Mapped[date] = mapped_column(Date())
    qty: Mapped[Decimal] = mapped_column(Dec())
    qty_remaining: Mapped[Decimal] = mapped_column(Dec())
    price: Mapped[Decimal] = mapped_column(Dec())  # за единицу, в валюте лота
    fees: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)  # все доп. затраты на весь лот (комиссия, НДС)
    vat: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)  # справочно: какая часть fees — НДС
    currency: Mapped[str] = mapped_column(String(8))
    fx_ccy_ils: Mapped[Decimal] = mapped_column(Dec())  # ₪ за 1 ед. валюты лота на дату покупки
    fx_usd_ils: Mapped[Decimal] = mapped_column(Dec())  # ₪ за $1 на дату покупки
    kind: Mapped[str] = mapped_column(String(16), default="buy")  # buy / transfer_in / swap_in / import
    origin_lot_id: Mapped[Optional[int]] = mapped_column(Integer(), nullable=True)
    # физические металлы
    purity: Mapped[Optional[Decimal]] = mapped_column(Dec(), nullable=True)  # проба, 0.999
    form: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # слиток / монета
    gross_weight_g: Mapped[Optional[Decimal]] = mapped_column(Dec(), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)

    asset = relationship("Asset")
    custodian = relationship("Custodian")
    disposals = relationship("Disposal", back_populates="lot", cascade="all, delete-orphan")


class Disposal(Base):
    """Реализация части лота: продажа или обмен (swap)."""

    __tablename__ = "disposals"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"))
    date: Mapped[date] = mapped_column(Date())
    qty: Mapped[Decimal] = mapped_column(Dec())
    price: Mapped[Decimal] = mapped_column(Dec())  # за единицу, в валюте продажи
    fee: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)  # комиссия, приходящаяся на эту часть
    currency: Mapped[str] = mapped_column(String(8))
    fx_ccy_ils: Mapped[Decimal] = mapped_column(Dec())
    fx_usd_ils: Mapped[Decimal] = mapped_column(Dec())
    kind: Mapped[str] = mapped_column(String(8), default="sale")  # sale / swap
    il_tax_withheld: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)  # удержано брокером, ₪
    group_id: Mapped[str] = mapped_column(String(36), default="")
    note: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)

    lot = relationship("Lot", back_populates="disposals")


class Transfer(Base):
    """Перевод актива между местами хранения. Не является продажей."""

    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    date: Mapped[date] = mapped_column(Date())
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    from_custodian_id: Mapped[int] = mapped_column(ForeignKey("custodians.id"))
    to_custodian_id: Mapped[int] = mapped_column(ForeignKey("custodians.id"))
    qty_sent: Mapped[Decimal] = mapped_column(Dec())
    qty_received: Mapped[Decimal] = mapped_column(Dec())
    fee_amount: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)  # денежная комиссия
    fee_currency: Mapped[str] = mapped_column(String(8), default="USD")
    flagged_exchange: Mapped[bool] = mapped_column(Boolean(), default=False)  # «по сути обмен» — проверить налог
    note: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)

    asset = relationship("Asset")
    from_custodian = relationship("Custodian", foreign_keys=[from_custodian_id])
    to_custodian = relationship("Custodian", foreign_keys=[to_custodian_id])
    parts = relationship("TransferPart", cascade="all, delete-orphan", back_populates="transfer")


class TransferPart(Base):
    """Какая часть какого лота переехала (нужно для журнала и отмены перевода)."""

    __tablename__ = "transfer_parts"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    transfer_id: Mapped[int] = mapped_column(ForeignKey("transfers.id"))
    source_lot_id: Mapped[int] = mapped_column(Integer())
    dest_lot_id: Mapped[int] = mapped_column(Integer())
    qty_sent: Mapped[Decimal] = mapped_column(Dec())
    fees_moved: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)

    transfer = relationship("Transfer", back_populates="parts")


class Dividend(Base):
    """Фактически полученная выплата."""

    __tablename__ = "dividends"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    custodian_id: Mapped[int] = mapped_column(ForeignKey("custodians.id"))
    ex_date: Mapped[Optional[date]] = mapped_column(Date(), nullable=True)
    pay_date: Mapped[date] = mapped_column(Date())
    gross: Mapped[Decimal] = mapped_column(Dec())
    currency: Mapped[str] = mapped_column(String(8))
    foreign_tax: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)  # удержано за рубежом, в валюте выплаты
    il_tax_withheld: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)  # удержано в Израиле, в валюте выплаты
    fx_ccy_ils: Mapped[Decimal] = mapped_column(Dec())
    fx_usd_ils: Mapped[Decimal] = mapped_column(Dec())
    note: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)

    asset = relationship("Asset")
    custodian = relationship("Custodian")


# ---------- кэш рыночных данных (только цены; личных данных здесь нет) ----------


class PricePoint(Base):
    __tablename__ = "price_points"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    date: Mapped[date] = mapped_column(Date(), primary_key=True)
    close: Mapped[Decimal] = mapped_column(Dec())


class Quote(Base):
    __tablename__ = "quotes"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    price: Mapped[Decimal] = mapped_column(Dec())
    currency: Mapped[str] = mapped_column(String(8), default="")
    as_of: Mapped[datetime] = mapped_column(DateTime())
    source: Mapped[str] = mapped_column(String(64), default="")


class FxPoint(Base):
    """Официальный курс Банка Израиля: ₪ за 1 единицу валюты."""

    __tablename__ = "fx_points"

    currency: Mapped[str] = mapped_column(String(8), primary_key=True)
    date: Mapped[date] = mapped_column(Date(), primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Dec())
    source: Mapped[str] = mapped_column(String(16), default="boi")


class DivPoint(Base):
    """История выплат на 1 акцию/пай (рыночные данные)."""

    __tablename__ = "div_points"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    ex_date: Mapped[date] = mapped_column(Date(), primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Dec())


# ---------- прочее ----------


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))
    params: Mapped[str] = mapped_column(Text())  # JSON
    result: Mapped[str] = mapped_column(Text(), default="{}")  # JSON: сводка и ряд для графика
    created_at: Mapped[datetime] = mapped_column(DateTime())


class CsvTemplate(Base):
    __tablename__ = "csv_templates"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    custodian_id: Mapped[int] = mapped_column(ForeignKey("custodians.id"), unique=True)
    mapping: Mapped[str] = mapped_column(Text())  # JSON


class ImportedRow(Base):
    """Отпечатки импортированных строк — защита от повторного импорта того же файла."""

    __tablename__ = "imported_rows"

    custodian_id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)


# ---------- пенсионные накопления ----------



class PensionAccount(Base):
    __tablename__ = "pension_accounts"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owners.id"))
    product_type: Mapped[str] = mapped_column(String(32))
    fund_name: Mapped[str] = mapped_column(String(128))
    track_key: Mapped[str] = mapped_column(String(32))
    track_custom: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    balance_ils: Mapped[Decimal] = mapped_column(Dec())
    balance_date: Mapped[date] = mapped_column(Date())
    w_israel_equity: Mapped[Decimal] = mapped_column(Dec())
    w_foreign_equity: Mapped[Decimal] = mapped_column(Dec())
    w_israel_bonds: Mapped[Decimal] = mapped_column(Dec())
    w_foreign_bonds: Mapped[Decimal] = mapped_column(Dec())
    w_cash_other: Mapped[Decimal] = mapped_column(Dec())
    weights_as_of: Mapped[Optional[date]] = mapped_column(Date(), nullable=True)
    fx_hedge_pct: Mapped[Decimal] = mapped_column(Dec(), default=ZERO)
    # привязка к официальным данным Минфина (data.gov.il)
    official_source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # gemel / pensia / bituach
    official_fund_id: Mapped[Optional[int]] = mapped_column(Integer(), nullable=True)
    official_fund_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)

    owner = relationship("Owner")
    balance_points = relationship(
        "BalancePoint", cascade="all, delete-orphan", back_populates="account", order_by="BalancePoint.date"
    )
    official = relationship(
        "OfficialMonthly", cascade="all, delete-orphan", back_populates="account", order_by="OfficialMonthly.period"
    )

    def weights(self) -> dict[str, Decimal]:
        return {c: getattr(self, f"w_{c}") for c in PENSION_CLASSES}

    def set_weights(self, weights: dict[str, Decimal]) -> None:
        for c in PENSION_CLASSES:
            setattr(self, f"w_{c}", Decimal(weights[c]))


class BalancePoint(Base):
    """Официальное значение баланса на дату (из отчёта или личного кабинета)."""

    __tablename__ = "balance_points"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("pension_accounts.id"))
    date: Mapped[date] = mapped_column(Date())
    balance_ils: Mapped[Decimal] = mapped_column(Dec())
    source: Mapped[str] = mapped_column(String(32), default="manual")

    account = relationship("PensionAccount", back_populates="balance_points")


class OfficialMonthly(Base):
    """Официальная месячная доходность трека и его экспозиции."""

    __tablename__ = "official_monthly"
    __table_args__ = (UniqueConstraint("account_id", "period"),)

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("pension_accounts.id"))
    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    return_pct: Mapped[Decimal] = mapped_column(Dec())
    w_israel_equity: Mapped[Optional[Decimal]] = mapped_column(Dec(), nullable=True)
    w_foreign_equity: Mapped[Optional[Decimal]] = mapped_column(Dec(), nullable=True)
    w_israel_bonds: Mapped[Optional[Decimal]] = mapped_column(Dec(), nullable=True)
    w_foreign_bonds: Mapped[Optional[Decimal]] = mapped_column(Dec(), nullable=True)
    w_cash_other: Mapped[Optional[Decimal]] = mapped_column(Dec(), nullable=True)
    # сырые официальные экспозиции, % от активов (если источник их даёт)
    stock_exposure_pct: Mapped[Optional[Decimal]] = mapped_column(Dec(), nullable=True)
    foreign_exposure_pct: Mapped[Optional[Decimal]] = mapped_column(Dec(), nullable=True)
    fx_exposure_pct: Mapped[Optional[Decimal]] = mapped_column(Dec(), nullable=True)
    source: Mapped[str] = mapped_column(String(255), default="")

    account = relationship("PensionAccount", back_populates="official")

    def composition(self) -> dict[str, Decimal] | None:
        values = {c: getattr(self, f"w_{c}") for c in PENSION_CLASSES}
        if any(v is None for v in values.values()):
            return None
        return values


# ---------- движок ----------

DEFAULT_OWNERS = ("Леонид", "Люба")


def get_engine(path: Path | str) -> Engine:
    if str(path) == ":memory:":
        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            "sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{path}", future=True, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def _add_missing_columns(engine: Engine) -> None:
    """Аддитивная миграция: добавляет колонки, появившиеся в новой версии приложения."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl_type = col.type.compile(dialect=engine.dialect)
                default = ""
                if col.default is not None and getattr(col.default, "is_scalar", False):
                    value = col.default.arg
                    if isinstance(value, bool):
                        default = f" DEFAULT {int(value)}"
                    elif isinstance(value, (int, Decimal)):
                        default = f" DEFAULT '{value}'"
                    elif isinstance(value, str):
                        default = " DEFAULT '" + value.replace("'", "''") + "'"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type}{default}'))


def init_db(engine: Engine, seed_owners: bool = True) -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    if seed_owners:
        with Session(engine) as s:
            if s.query(Owner).count() == 0:
                for name in DEFAULT_OWNERS:
                    s.add(Owner(name=name))
                s.commit()


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_setting(session: Session, key: str, default: str | None = None) -> str | None:
    row = session.get(Setting, key)
    return row.value if row else default


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
