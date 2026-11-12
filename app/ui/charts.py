"""Графики Plotly в едином стиле: фиксированные цвета по сущностям, одна ось, тонкие линии."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

# Категориальная палитра (порядок фиксирован; проверена на различимость при дальтонизме).
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
GOOD, WARNING, CRITICAL, NEUTRAL = "#0ca30c", "#fab219", "#d03b3b", "#8a8984"

# Цвет закреплён за классом актива, а не за его местом в списке.
CLASS_COLORS = {
    "Акции": PALETTE[0],
    "ETF / фонды": PALETTE[1],
    "Криптовалюта": PALETTE[2],
    "Физические металлы": PALETTE[3],
    "Наличные / валюта": PALETTE[4],
    "Другое (депозиты и пр.)": PALETTE[5],
    "Пенсионные накопления": PALETTE[6],
}


def _layout(fig: go.Figure, title: str | None, height: int, y_suffix: str = "", legend: bool = True) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=15)) if title else None,
        height=height,
        margin=dict(l=8, r=8, t=44 if title else 12, b=8),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0),
        hovermode="x unified",
        colorway=PALETTE,
        yaxis=dict(ticksuffix=y_suffix, gridcolor="rgba(128,128,128,0.18)", zeroline=False),
        xaxis=dict(showgrid=False),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def lines(
    series: dict[str, pd.Series], title: str | None = None, y_suffix: str = "", height: int = 340,
    colors: dict[str, str] | None = None, markers: dict[str, pd.Series] | None = None, digits: int = 2,
) -> go.Figure:
    """Линии по общей оси. Больше 8 рядов не рисуем — лишние отбрасываются вызывающим кодом."""
    fig = go.Figure()
    for i, (name, s) in enumerate(list(series.items())[:8]):
        color = (colors or {}).get(name, PALETTE[i % len(PALETTE)])
        fig.add_trace(go.Scatter(
            x=s.index, y=s.values, name=name, mode="lines", line=dict(width=2, color=color),
            hovertemplate=f"%{{y:,.{digits}f}}{y_suffix}<extra>{name}</extra>",
        ))
    for name, s in (markers or {}).items():
        fig.add_trace(go.Scatter(
            x=s.index, y=s.values, name=name, mode="markers",
            marker=dict(size=9, color=NEUTRAL, line=dict(width=2, color="white")),
            hovertemplate=f"%{{y:,.{digits}f}}{y_suffix}<extra>{name}</extra>",
        ))
    return _layout(fig, title, height, y_suffix, legend=len(series) + len(markers or {}) > 1)


def stacked_area(frame: pd.DataFrame, title: str | None = None, y_suffix: str = "", height: int = 340,
                 colors: dict[str, str] | None = None) -> go.Figure:
    fig = go.Figure()
    for i, name in enumerate(list(frame.columns)[:8]):
        color = (colors or {}).get(name, PALETTE[i % len(PALETTE)])
        fig.add_trace(go.Scatter(
            x=frame.index, y=frame[name], name=name, mode="lines", stackgroup="one",
            line=dict(width=1, color=color), hovertemplate=f"%{{y:,.0f}}{y_suffix}<extra>{name}</extra>",
        ))
    return _layout(fig, title, height, y_suffix, legend=len(frame.columns) > 1)


def hbar(labels: list[str], values: list[float], title: str | None = None, suffix: str = "",
         colors: list[str] | None = None, threshold: float | None = None, height: int | None = None) -> go.Figure:
    """Горизонтальные столбцы (доли, сравнение величин). threshold — вертикальная линия порога."""
    order = list(reversed(range(len(labels))))
    fig = go.Figure(go.Bar(
        x=[values[i] for i in order], y=[labels[i] for i in order], orientation="h",
        marker=dict(color=[(colors or [PALETTE[0]] * len(labels))[i] for i in order], cornerradius=4),
        text=[f"{values[i]:,.1f}{suffix}" for i in order], textposition="auto",
        hovertemplate=f"%{{y}}: %{{x:,.2f}}{suffix}<extra></extra>",
    ))
    if threshold is not None:
        fig.add_vline(x=threshold, line=dict(color=CRITICAL, width=2, dash="dash"))
    _layout(fig, title, height or max(160, 44 * len(labels) + 60), suffix, legend=False)
    fig.update_layout(hovermode="closest", bargap=0.35, xaxis=dict(ticksuffix=suffix, showgrid=True,
                                                                  gridcolor="rgba(128,128,128,0.18)"),
                      yaxis=dict(ticksuffix="", showgrid=False))
    return fig


def bars(x: list, series: dict[str, list[float]], title: str | None = None, y_suffix: str = "",
         height: int = 320) -> go.Figure:
    fig = go.Figure()
    for i, (name, values) in enumerate(list(series.items())[:8]):
        fig.add_trace(go.Bar(x=x, y=values, name=name, marker=dict(color=PALETTE[i], cornerradius=4),
                             hovertemplate=f"%{{y:,.2f}}{y_suffix}<extra>{name}</extra>"))
    _layout(fig, title, height, y_suffix, legend=len(series) > 1)
    fig.update_layout(barmode="group", bargap=0.3)
    return fig
