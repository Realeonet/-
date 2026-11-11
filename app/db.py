"""Модели SQLite (SQLAlchemy 2.x). Деньги и веса хранятся как строки Decimal — без потери точности."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import Date, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.domain.pension import CLASSES


class Dec(TypeDecorator):
    """Decimal, хранится как текст."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        return None if value is None else str(Decimal(value))

    def process_result_value(self, value, dialect):  # noqa: ANN001
        return None if value is None else Decimal(value)


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text())


class PensionAccount(Base):
    __tablename__ = "pension_accounts"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), default="я")
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
    fx_hedge_pct: Mapped[Decimal] = mapped_column(Dec())
    notes: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)

    balance_points = relationship(
        "BalancePoint",
        cascade="all, delete-orphan",
        back_populates="account",
        order_by="BalancePoint.date",
    )
    official = relationship(
        "OfficialMonthly",
        cascade="all, delete-orphan",
        back_populates="account",
        order_by="OfficialMonthly.period",
    )

    def weights(self) -> dict[str, Decimal]:
        return {c: getattr(self, f"w_{c}") for c in CLASSES}

    def set_weights(self, weights: dict[str, Decimal]) -> None:
        for c in CLASSES:
            setattr(self, f"w_{c}", Decimal(weights[c]))


class BalancePoint(Base):
    """Ручные/официальные значения баланса на дату (из отчёта или личного кабинета)."""

    __tablename__ = "balance_points"

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("pension_accounts.id"))
    date: Mapped[date] = mapped_column(Date())
    balance_ils: Mapped[Decimal] = mapped_column(Dec())
    source: Mapped[str] = mapped_column(String(32), default="manual")

    account = relationship("PensionAccount", back_populates="balance_points")


class OfficialMonthly(Base):
    """Официальная месячная доходность трека (и состав, если есть в импорте)."""

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
    source: Mapped[str] = mapped_column(String(255), default="")

    account = relationship("PensionAccount", back_populates="official")

    def composition(self) -> dict[str, Decimal] | None:
        values = {c: getattr(self, f"w_{c}") for c in CLASSES}
        if any(v is None for v in values.values()):
            return None
        return values


def get_engine(path: Path | str) -> Engine:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path}"
    else:
        url = "sqlite:///:memory:"
    return create_engine(url, future=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_setting(session: Session, key: str) -> str | None:
    row = session.get(Setting, key)
    return row.value if row else None


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
