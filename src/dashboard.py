"""
Equity Research Lab — Streamlit Dashboard
===========================================

Interface web local pra explorar sinais, fundamentals e tendências.

USO:
    streamlit run src/dashboard.py

ABAS:
    📊 Daily View        — top 20 de hoje com fundamentals completos
    🚨 Red Flags         — tickers com insider selling, downgrades, etc.
    💎 Quality Stocks    — FCF positivo + ROE alto + sem red flags
    🏭 Setores           — distribuição setorial ao longo do tempo
    🔄 Histórico         — sinais por dia
    🔍 Ticker Drill-down — análise individual de um ticker específico
    📈 Outcomes          — performance dos sinais antigos (quando houver dados)
"""

from __future__ import annotations

from pathlib import Path

import altair as alt
import duckdb
import pandas as pd
import polars as pl
import streamlit as st

PROJECT_DIR = Path(__file__).parent.parent
DB_PATH = PROJECT_DIR / "data" / "research_lab.duckdb"

# ============================================================
# CONFIG DA PÁGINA
# ============================================================

st.set_page_config(
    page_title="Equity Research Lab",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Estilos sutis (respeita dark/light mode do Streamlit)
st.markdown(
    """
    <style>
    /* Espacamento extra entre métricas */
    [data-testid="stMetric"] {
        padding: 0.5rem 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# CONEXÃO DB
# ============================================================


def _query(sql: str, params: list | None = None):
    """Abre conexão por query em vez de manter aberta — evita locks com backtest/cron."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        if params:
            result = conn.execute(sql, params)
        else:
            result = conn.execute(sql)
        return result.fetchall(), [d[0] for d in result.description]
    finally:
        conn.close()


def _query_df(sql: str, params: list | None = None) -> pd.DataFrame:
    """Versão que retorna DataFrame."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        if params:
            return conn.execute(sql, params).df()
        return conn.execute(sql).df()
    finally:
        conn.close()


@st.cache_data(ttl=300)  # cache por 5 min
def get_available_dates():
    rows, _ = _query("SELECT DISTINCT signal_date FROM signals ORDER BY signal_date DESC")
    return [r[0] for r in rows]


@st.cache_data(ttl=300)
def get_signals_for_date(date) -> pd.DataFrame:
    return _query_df(
        "SELECT * FROM signals WHERE signal_date = ? ORDER BY universe, rank_position",
        [date],
    )


@st.cache_data(ttl=300)
def get_ticker_history(ticker: str) -> pd.DataFrame:
    return _query_df(
        "SELECT * FROM signals WHERE ticker = ? ORDER BY signal_date DESC",
        [ticker.upper()],
    )


@st.cache_data(ttl=300)
def get_db_stats():
    rows, _ = _query(
        """
        SELECT
            (SELECT COUNT(*) FROM signals) AS total_signals,
            (SELECT COUNT(DISTINCT ticker) FROM signals) AS unique_tickers,
            (SELECT COUNT(DISTINCT signal_date) FROM signals) AS days_of_data,
            (SELECT COUNT(*) FROM analyses_llm) AS total_analyses,
            (SELECT COUNT(*) FROM signal_outcomes) AS total_outcomes
        """
    )
    if not rows:
        return {k: 0 for k in [
            "total_signals", "unique_tickers", "days_of_data",
            "total_analyses", "total_outcomes"
        ]}
    r = rows[0]
    return {
        "total_signals": r[0],
        "unique_tickers": r[1],
        "days_of_data": r[2],
        "total_analyses": r[3],
        "total_outcomes": r[4],
    }


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.title("📊 Research Lab")

    if not DB_PATH.exists():
        st.error(f"DB não existe: {DB_PATH}")
        st.stop()

    available_dates = get_available_dates()
    if not available_dates:
        st.warning("Banco vazio — rode o screener primeiro")
        st.stop()

    selected_date = st.selectbox(
        "📅 Data dos sinais",
        options=available_dates,
        index=0,
        format_func=lambda d: d.strftime("%d/%m/%Y (%a)"),
    )

    st.divider()

    # Stats do banco
    stats = get_db_stats()
    st.metric("Sinais totais", f"{stats['total_signals']:,}")
    st.metric("Tickers únicos", f"{stats['unique_tickers']:,}")
    st.metric("Dias de dados", f"{stats['days_of_data']:,}")
    st.metric("Análises LLM", f"{stats['total_analyses']:,}")
    st.metric("Outcomes medidos", f"{stats['total_outcomes']:,}")

    st.divider()
    st.caption("Dados via DuckDB local")
    st.caption(f"`{DB_PATH.name}`")


# ============================================================
# TÍTULO
# ============================================================

st.title("📊 Equity Research Lab")
st.caption(f"Sinais de **{selected_date.strftime('%d/%m/%Y (%A)')}**")

df = get_signals_for_date(selected_date)

if df.empty:
    st.warning("Sem dados pra essa data.")
    st.stop()

# ============================================================
# MÉTRICAS DE RESUMO (CARDS NO TOPO)
# ============================================================

col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.metric("Sinais hoje", len(df))

with col2:
    n_universe_a = len(df[df["universe"] == "A"])
    n_universe_b = len(df[df["universe"] == "B"])
    st.metric("Universo A / B", f"{n_universe_a} / {n_universe_b}")

with col3:
    if "insider_net_value_6m" in df.columns:
        red_flags = df[df["insider_net_value_6m"] < -1_000_000].shape[0]
        st.metric("🚨 Insider red flags", red_flags)
    else:
        st.metric("🚨 Insider red flags", "n/a")

with col4:
    if "free_cash_flow" in df.columns and "return_on_equity" in df.columns:
        quality = df[
            (df["free_cash_flow"] > 0) & (df["return_on_equity"] > 0.15)
        ].shape[0]
        st.metric("💎 Quality stocks", quality)
    else:
        st.metric("💎 Quality stocks", "n/a")

with col5:
    top_sector = (
        df.groupby("sector").size().sort_values(ascending=False).head(1).index[0]
    )
    st.metric("Setor dominante", top_sector)

# ============================================================
# ABAS
# ============================================================

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(
    [
        "📊 Daily View",
        "🚨 Red Flags",
        "💎 Quality Stocks",
        "🏭 Setores",
        "🔄 Histórico",
        "🔍 Ticker Drill-down",
        "📈 Outcomes",
    ]
)

# ============================================================
# TAB 1: DAILY VIEW
# ============================================================

with tab1:
    st.subheader("Top 20 do dia")
    st.caption("Ordenado por composite score dentro de cada universo")

    # Universo A
    df_a = df[df["universe"] == "A"].copy()
    if not df_a.empty:
        st.markdown("### 🏢 Universo A (Mid/Large Caps)")
        display_cols_a = [
            "rank_position",
            "ticker",
            "sector",
            "close",
            "rsi14",
            "composite_score",
            "pct_below_52w_high",
            "pe_forward",
            "ev_to_ebitda",
            "revenue_growth_yoy",
            "free_cash_flow",
            "insider_net_value_6m",
            "analyst_rating",
            "target_upside_pct",
        ]
        df_a_display = df_a[[c for c in display_cols_a if c in df_a.columns]].copy()
        if "free_cash_flow" in df_a_display.columns:
            df_a_display["free_cash_flow"] = (
                df_a_display["free_cash_flow"] / 1_000_000
            ).round(1)
            df_a_display.rename(columns={"free_cash_flow": "fcf_mm"}, inplace=True)
        if "insider_net_value_6m" in df_a_display.columns:
            df_a_display["insider_net_value_6m"] = (
                df_a_display["insider_net_value_6m"] / 1_000_000
            ).round(1)
            df_a_display.rename(
                columns={"insider_net_value_6m": "insider_mm"}, inplace=True
            )
        st.dataframe(df_a_display, width="stretch", hide_index=True)

    # Universo B
    df_b = df[df["universe"] == "B"].copy()
    if not df_b.empty:
        st.markdown("### 🏪 Universo B (Small Caps)")
        df_b_display = df_b[[c for c in display_cols_a if c in df_b.columns]].copy()
        if "free_cash_flow" in df_b_display.columns:
            df_b_display["free_cash_flow"] = (
                df_b_display["free_cash_flow"] / 1_000_000
            ).round(1)
            df_b_display.rename(columns={"free_cash_flow": "fcf_mm"}, inplace=True)
        if "insider_net_value_6m" in df_b_display.columns:
            df_b_display["insider_net_value_6m"] = (
                df_b_display["insider_net_value_6m"] / 1_000_000
            ).round(1)
            df_b_display.rename(
                columns={"insider_net_value_6m": "insider_mm"}, inplace=True
            )
        st.dataframe(df_b_display, width="stretch", hide_index=True)

# ============================================================
# TAB 2: RED FLAGS
# ============================================================

with tab2:
    st.subheader("🚨 Tickers com Red Flags")

    if "insider_net_value_6m" not in df.columns:
        st.warning("Dados de insider não disponíveis ainda.")
    else:
        st.markdown("### Insider selling pesado (>$1M vendido em 6 meses)")
        red_flag_df = df[df["insider_net_value_6m"] < -1_000_000].copy()
        if red_flag_df.empty:
            st.success("✅ Nenhum red flag de insider hoje")
        else:
            red_flag_df["insider_value_mm"] = (
                red_flag_df["insider_net_value_6m"] / 1_000_000
            ).round(2)
            display = red_flag_df[
                [
                    "ticker",
                    "sector",
                    "close",
                    "insider_n_buys_6m",
                    "insider_n_sells_6m",
                    "insider_value_mm",
                    "insider_top_sellers",
                ]
            ].sort_values("insider_value_mm")
            st.dataframe(display, width="stretch", hide_index=True)

    st.divider()

    # Analyst downgrades
    if "analyst_downgrades_90d" in df.columns:
        st.markdown("### Analyst downgrades > upgrades últimos 90 dias")
        down_df = df[
            (df["analyst_downgrades_90d"] > df["analyst_upgrades_90d"])
            & (df["analyst_downgrades_90d"] > 0)
        ].copy()
        if down_df.empty:
            st.success("✅ Nenhum ticker com momentum negativo de analistas")
        else:
            display = down_df[
                [
                    "ticker",
                    "sector",
                    "close",
                    "analyst_upgrades_90d",
                    "analyst_downgrades_90d",
                    "analyst_rating",
                ]
            ]
            st.dataframe(display, width="stretch", hide_index=True)

    st.divider()

    # Earnings beats baixos
    if "earnings_beats_4q" in df.columns:
        st.markdown("### Earnings track record fraco (<2 beats em 4 trimestres)")
        weak_df = df[df["earnings_beats_4q"] < 2].copy()
        if weak_df.empty:
            st.success("✅ Todos os tickers com track record decente")
        else:
            display = weak_df[
                [
                    "ticker",
                    "sector",
                    "close",
                    "earnings_beats_4q",
                    "earnings_avg_surprise_pct",
                    "pe_forward",
                ]
            ]
            st.dataframe(display, width="stretch", hide_index=True)


# ============================================================
# TAB 3: QUALITY STOCKS
# ============================================================

with tab3:
    st.subheader("💎 Quality Stocks (FCF positivo + ROE alto)")

    needed = ["free_cash_flow", "return_on_equity"]
    if not all(c in df.columns for c in needed):
        st.warning("Dados de FCF / ROE não disponíveis ainda.")
    else:
        quality_df = df[
            (df["free_cash_flow"] > 0) & (df["return_on_equity"] > 0.15)
        ].copy()

        if quality_df.empty:
            st.info("Nenhum ticker atende ambos os critérios hoje.")
        else:
            quality_df["fcf_mm"] = (quality_df["free_cash_flow"] / 1_000_000).round(1)
            quality_df["roe_pct"] = (quality_df["return_on_equity"] * 100).round(1)
            quality_df["op_margin_pct"] = (
                quality_df["operating_margin"] * 100
            ).round(1)
            if "net_debt" in quality_df.columns:
                quality_df["net_debt_mm"] = (quality_df["net_debt"] / 1_000_000).round(
                    1
                )
            display_cols = [
                "ticker",
                "sector",
                "close",
                "fcf_mm",
                "roe_pct",
                "op_margin_pct",
                "pe_forward",
                "earnings_beats_4q",
            ]
            if "net_debt_mm" in quality_df.columns:
                display_cols.insert(5, "net_debt_mm")
            st.dataframe(
                quality_df[[c for c in display_cols if c in quality_df.columns]]
                .sort_values("roe_pct", ascending=False),
                width="stretch",
                hide_index=True,
            )


# ============================================================
# TAB 4: SETORES
# ============================================================

with tab4:
    st.subheader("🏭 Distribuição setorial")

    sector_dist = (
        df.groupby(["universe", "sector"])
        .agg(n=("ticker", "size"), avg_score=("composite_score", "mean"))
        .reset_index()
        .sort_values(["universe", "n"], ascending=[True, False])
    )

    col_a, col_b = st.columns(2)

    with col_a:
        chart = (
            alt.Chart(sector_dist[sector_dist["universe"] == "A"])
            .mark_bar()
            .encode(
                x=alt.X("n:Q", title="Nº de sinais"),
                y=alt.Y("sector:N", sort="-x", title=None),
                color=alt.value("#0066cc"),
                tooltip=["sector", "n", alt.Tooltip("avg_score:Q", format=".1f")],
            )
            .properties(title="Universo A (Mid/Large)", height=300)
        )
        st.altair_chart(chart, width="stretch")

    with col_b:
        chart = (
            alt.Chart(sector_dist[sector_dist["universe"] == "B"])
            .mark_bar()
            .encode(
                x=alt.X("n:Q", title="Nº de sinais"),
                y=alt.Y("sector:N", sort="-x", title=None),
                color=alt.value("#22aa44"),
                tooltip=["sector", "n", alt.Tooltip("avg_score:Q", format=".1f")],
            )
            .properties(title="Universo B (Small)", height=300)
        )
        st.altair_chart(chart, width="stretch")

    st.divider()
    st.caption("Tabela completa:")
    st.dataframe(sector_dist, width="stretch", hide_index=True)


# ============================================================
# TAB 5: HISTÓRICO
# ============================================================

with tab5:
    st.subheader("🔄 Histórico de execuções")

    history = _query_df(
        """
        SELECT
            signal_date,
            universe,
            COUNT(*) AS n_sinais,
            ROUND(AVG(composite_score), 1) AS avg_score
        FROM signals
        GROUP BY signal_date, universe
        ORDER BY signal_date DESC, universe
        """
    )

    if history.empty:
        st.info("Sem histórico ainda.")
    else:
        # Pivot pra ter A e B em colunas
        pivot = history.pivot(
            index="signal_date", columns="universe", values="n_sinais"
        ).fillna(0)
        pivot.columns = [f"Univ. {c}" for c in pivot.columns]
        pivot["Total"] = pivot.sum(axis=1)
        pivot = pivot.reset_index().sort_values("signal_date", ascending=False)
        st.dataframe(pivot, width="stretch", hide_index=True)

        # Gráfico
        st.divider()
        history_melted = history.copy()
        chart = (
            alt.Chart(history_melted)
            .mark_line(point=True)
            .encode(
                x=alt.X("signal_date:T", title="Data"),
                y=alt.Y("avg_score:Q", title="Composite Score médio"),
                color="universe:N",
                tooltip=["signal_date", "universe", "n_sinais", "avg_score"],
            )
            .properties(height=300, title="Composite Score médio ao longo do tempo")
        )
        st.altair_chart(chart, width="stretch")


# ============================================================
# TAB 6: TICKER DRILL-DOWN
# ============================================================

with tab6:
    st.subheader("🔍 Análise individual de um ticker")

    # Lista todos os tickers que já apareceram
    rows, _ = _query("SELECT DISTINCT ticker FROM signals ORDER BY ticker")
    all_tickers = [r[0] for r in rows]

    ticker = st.selectbox("Escolha um ticker", options=all_tickers)

    if ticker:
        history = get_ticker_history(ticker)
        if history.empty:
            st.info("Sem dados pra esse ticker.")
        else:
            # Última observação (mais recente)
            latest = history.iloc[0]

            st.markdown(f"### {ticker} — {latest.get('sector', '?')}")

            # Cards com métricas
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Preço", f"${latest.get('close', 0):.2f}")
            c2.metric("RSI(14)", f"{latest.get('rsi14', 0):.1f}")
            c3.metric("Composite Score", f"{latest.get('composite_score', 0):.1f}")
            c4.metric("Dias no top 20", history["signal_date"].nunique())

            # Fundamentals em cards
            st.divider()
            st.markdown("#### 💰 Fundamentals")
            f1, f2, f3, f4 = st.columns(4)
            f1.metric("P/E Forward", f"{latest.get('pe_forward', 0):.1f}" if latest.get('pe_forward') else "n/a")
            f2.metric("EV/EBITDA", f"{latest.get('ev_to_ebitda', 0):.1f}" if latest.get('ev_to_ebitda') else "n/a")
            f3.metric(
                "ROE",
                f"{latest.get('return_on_equity', 0) * 100:.1f}%"
                if latest.get("return_on_equity")
                else "n/a",
            )
            f4.metric(
                "FCF (M)",
                f"${latest.get('free_cash_flow', 0) / 1_000_000:.1f}M"
                if latest.get("free_cash_flow")
                else "n/a",
            )

            # Crescimento
            g1, g2, g3, g4 = st.columns(4)
            g1.metric(
                "Receita YoY",
                f"{latest.get('revenue_growth_yoy', 0) * 100:.1f}%"
                if latest.get("revenue_growth_yoy") is not None
                else "n/a",
            )
            g2.metric(
                "Lucro YoY",
                f"{latest.get('earnings_growth_yoy', 0) * 100:.1f}%"
                if latest.get("earnings_growth_yoy") is not None
                else "n/a",
            )
            g3.metric(
                "Margem Op.",
                f"{latest.get('operating_margin', 0) * 100:.1f}%"
                if latest.get("operating_margin") is not None
                else "n/a",
            )
            g4.metric(
                "Beats 4Q",
                f"{int(latest.get('earnings_beats_4q', 0))}/4"
                if latest.get("earnings_beats_4q") is not None
                else "n/a",
            )

            # Analyst + Insider
            st.divider()
            a1, a2, a3, a4 = st.columns(4)
            a1.metric(
                "Analyst Rating",
                f"{latest.get('analyst_rating', 0):.2f}"
                if latest.get("analyst_rating")
                else "n/a",
            )
            a2.metric(
                "Target Upside",
                f"{latest.get('target_upside_pct', 0):.1f}%"
                if latest.get("target_upside_pct") is not None
                else "n/a",
            )
            a3.metric(
                "Insider 6m ($M)",
                f"{latest.get('insider_net_value_6m', 0) / 1_000_000:.1f}"
                if latest.get("insider_net_value_6m") is not None
                else "n/a",
            )
            a4.metric(
                "Beta",
                f"{latest.get('beta', 0):.2f}"
                if latest.get("beta") is not None
                else "n/a",
            )

            # Histórico de aparição no top 20
            st.divider()
            st.markdown(f"#### 📅 Histórico de aparição ({len(history)} ocorrências)")
            hist_display = history[
                [
                    "signal_date",
                    "universe",
                    "rank_position",
                    "close",
                    "rsi14",
                    "composite_score",
                ]
            ].copy()
            st.dataframe(hist_display, width="stretch", hide_index=True)


# ============================================================
# TAB 7: OUTCOMES
# ============================================================

with tab7:
    st.subheader("📈 Outcomes (performance dos sinais antigos)")

    outcomes = _query_df(
        """
        SELECT
            days_elapsed AS dias,
            COUNT(*) AS n_outcomes,
            ROUND(AVG(CASE WHEN hit_target_1 THEN 1.0 ELSE 0.0 END) * 100, 1) AS hit_rate_t1_pct,
            ROUND(AVG(CASE WHEN hit_target_2 THEN 1.0 ELSE 0.0 END) * 100, 1) AS hit_rate_t2_pct,
            ROUND(AVG(CASE WHEN hit_stop THEN 1.0 ELSE 0.0 END) * 100, 1) AS hit_stop_pct,
            ROUND(AVG(r_multiple), 2) AS avg_r_multiple,
            ROUND(AVG(excess_return), 2) AS avg_excess_return_pct
        FROM signal_outcomes
        WHERE NOT is_open
        GROUP BY days_elapsed
        ORDER BY days_elapsed
        """
    )

    if outcomes.empty:
        st.info(
            "Sem outcomes coletados ainda. "
            "Precisamos de pelo menos 7 dias depois dos primeiros sinais "
            "pra começar a medir."
        )
    else:
        st.dataframe(outcomes, width="stretch", hide_index=True)

        # Hit rate ao longo do tempo
        chart = (
            alt.Chart(outcomes)
            .mark_bar()
            .encode(
                x="dias:O",
                y="hit_rate_t1_pct:Q",
                tooltip=["dias", "n_outcomes", "hit_rate_t1_pct", "avg_r_multiple"],
            )
            .properties(title="Hit rate (target 1) por janela temporal")
        )
        st.altair_chart(chart, width="stretch")


# ============================================================
# FOOTER
# ============================================================

st.divider()
st.caption(
    "📊 Equity Research Lab — Streamlit Dashboard | "
    "Rodando localmente em DuckDB | "
    f"DB: `{DB_PATH.name}` | "
    "Não constitui recomendação de investimento."
)
