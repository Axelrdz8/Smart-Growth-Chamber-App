import os
import re
import requests
import pandas as pd
from datetime import datetime, date, timedelta
import streamlit as st
import plotly.express as px # type: ignore

CHANNEL_SOIL = 2869579
CHANNEL_ENV  = 2913085
DEFAULT_TIMEZONE = "America/Monterrey"
DEFAULT_RES_MIN = 10
MAX_POINTS = 8000

# ---- UMBRALES para las 4 tarjetas del resumen ----
LIMITS_MAIN = {
    "soil_moist": (25, 50),   # %
    "air_temp":   (20, 30),   # °C
    "air_hum":    (40, 60),   # %
    "soil_ph":    (5.5, 6.8)  # pH
}

def _in_range(val, lo_hi):
    lo, hi = lo_hi
    return (val is not None) and (lo <= val <= hi)

def _bg_for_main(metric_key, value):
    """
    Devuelve color de fondo para la tarjeta (rojo si fuera de rango, gris si OK).
    """
    if value is None:
        return "#3F4F61"                    # sin dato -> gris
    return "red" if not _in_range(value, LIMITS_MAIN[metric_key]) else "#3F4F61"

st.set_page_config(page_title="Smart Growth Chamber", page_icon="🌱", layout="wide")

# -------------------- estilos CSS tarjetas + titulo --------------------
st.markdown("""
<style>
/* ---- Ajuste general de tarjetas ---- */
.kpi-card {
    background-color: #3F4F61;
    padding: 20px;
    border-radius: 12px;
    text-align: center;
    margin-bottom: 15px;
}
.kpi-card h2 { font-size: 2em; margin: 0; color: white; }
.kpi-card p  { margin: 0; font-size: 1.1em; color: white; }

/* ---- Título Dashboard ---- */
.dashboard-title {
    margin-top: 0.9rem !important;     /* compacta en móvil */
    margin-bottom: 0.2rem !important;
}

/* ---- Compactar el padding del contenedor en móvil ---- */
@media (max-width: 767px) {
    .block-container {
        padding-top: 0.5rem !important;
        padding-bottom: 0.5rem !important;
    }
}

/* ---- En escritorio, un poco más de aire ---- */
@media (min-width: 768px) {
    .dashboard-title {
        margin-top: 0.8rem !important;
        margin-bottom: 1.2rem !important;
    }
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 1rem !important;
    }
}

/* Ajuste del título en el sidebar */
.sidebar-title {
    margin-top: 0.2rem !important;
    margin-bottom: 0.5rem !important;
}

/* Responsive en el sidebar */
@media (min-width: 768px) {
    .sidebar-title {
        margin-top: 0.6rem !important;
        margin-bottom: 1rem !important;
    }
}
</style>
""", unsafe_allow_html=True)

def _clean(s: str) -> str:
    if not s:
        return ""
    return re.sub(r'\s+', ' ', s).strip()

@st.cache_data(ttl=300)
def fetch_thingspeak(channel_id: int, timezone: str, use_range: bool,
                     start_date: date, end_date: date, max_points: int,
                     read_key: str | None):
    base = f"https://api.thingspeak.com/channels/{channel_id}/feeds.json"
    params = {"timezone": timezone}
    if use_range:
        params.update({"start": f"{start_date} 00:00:00",
                       "end":   f"{end_date} 23:59:59"})
    else:
        params["results"] = max_points
    if read_key:
        params["api_key"] = read_key

    r = requests.get(base, params=params, timeout=30)
    r.raise_for_status()
    payload = r.json()
    channel_meta = payload.get("channel", {})
    feeds = payload.get("feeds", [])
    df = pd.DataFrame(feeds)

    # Si no hay datos, sal temprano
    if df.empty:
        return channel_meta, pd.DataFrame()

    # 1) Parsear siempre como tz-aware (UTC)
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
    df = df.dropna(subset=["created_at"])

    # 2) Convertir a la zona elegida
    df["created_at_local"] = df["created_at"].dt.tz_convert(timezone)

    # 3) Usar índice en hora local y dejarlo naive (sin tz) para resample
    df = df.set_index("created_at_local").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    # Campos numéricos
    for c in [c for c in df.columns if c.startswith("field")]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return channel_meta, df

def label_map_from_meta(meta: dict):
    mapping = {}
    for i in range(1, 9):
        key = f"field{i}"
        name = _clean(meta.get(key, "")) or key
        mapping[key] = name
    return mapping

def latest_value(df: pd.DataFrame, field: str):
    if df.empty or field not in df.columns:
        return None, None
    last_row = df[[field]].dropna().tail(1)
    if last_row.empty:
        return None, None
    val = float(last_row.iloc[0, 0])
    ts = last_row.index[-1]
    return val, ts

def resample_series(df, field, minutes: int):
    if df.empty or field not in df.columns:
        return pd.Series(dtype=float)
    return df[field].resample(f"{int(minutes)}T", label="right").mean()

def kpi_card_full(title, value, unit="", icon="", ts=None, bg_color="#3F4F61"):
    display_val = "—" if value is None else f"{value:.2f} {unit}".strip()
    ts_txt = "" if ts is None else f"<p><em>{ts}</em></p>"
    st.markdown(
        f"""
        <div class="kpi-card" style="background-color:{bg_color};">
            <h2>{icon} {title}: {display_val}</h2>
            {ts_txt}
        </div>
        """,
        unsafe_allow_html=True,
    )

def kpi_card(col, title, value, unit="", icon="", bg_color="#3F4F61"):
    display_val = "—" if value is None else f"{value:.2f} {unit}".strip()
    with col:
        st.markdown(
            f"""
            <div class="kpi-card" style="background-color:{bg_color};">
                <h2>{icon} {title}</h2>
                <p>{display_val}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

with st.sidebar:
    st.markdown('<h2 class="sidebar-title">Smart Growth Chamber 🌱🌱🌱📈📊📉🌱🌱🌱</h2>', unsafe_allow_html=True)
    tz = st.text_input("Zona horaria", value=DEFAULT_TIMEZONE)
    res_min = st.number_input("Resampleo (min)", value=DEFAULT_RES_MIN, min_value=1, step=1)
    today = date.today()
    use_range = st.checkbox("Usar rango de fechas", value=False)
    start_date = st.date_input("Inicio", value=today - timedelta(days=1))
    end_date = st.date_input("Fin", value=today)
    page = st.radio("Ver", [
        "Resumen", "Soil Temperature", "Soil Moisture", "Soil Conductivity", 
        "Soil pH", "Soil N concentration", "Soil P concentration", "Soil K concentration", 
        "Air Temperature", "Air Humidity", "Luminosity", "CO2 concentration"])
    if st.button("🔄 Actualizar ahora"):
        st.cache_data.clear()   # limpia cache
        st.rerun()              # vuelve a ejecutar y refetch (Streamlit 1.27+)


READ_KEY = os.getenv("TS_READ")

meta_soil, df_soil = fetch_thingspeak(CHANNEL_SOIL, tz, use_range, start_date, end_date, MAX_POINTS, READ_KEY)
meta_env,  df_env  = fetch_thingspeak(CHANNEL_ENV,  tz, use_range, start_date, end_date, MAX_POINTS, READ_KEY)

labels_soil = label_map_from_meta(meta_soil or {})
labels_env  = label_map_from_meta(meta_env  or {})

# Título
st.markdown('<h2 class="dashboard-title">Dashboard</h2>', unsafe_allow_html=True)

def plot_metric(df, field, title, y_label, unit="", icon=""):
    val, ts = latest_value(df, field)
    kpi_card_full(title, val, unit=unit, icon=icon, ts=ts)
    if not field or df.empty or field not in df.columns:
        st.warning("Campo no disponible.")
        return
    series = resample_series(df, field, res_min)
    fig = px.line(series, labels={"index": "", "value": y_label})
    fig.update_layout(showlegend=False, margin=dict(l=20, r=20, t=10, b=20))
    fig.update_traces(name="", showlegend=False)
    fig.update_xaxes(title=None)
    fig.update_yaxes(title=y_label)
    st.plotly_chart(fig, use_container_width=True)

if page == "Soil Temperature":
    plot_metric(df_soil, "field1", "Soil Temperature", "°C", unit="°C", icon="🌡️")
elif page == "Soil Moisture":
    plot_metric(df_soil, "field2", "Soil Moisture", "%", unit="%", icon="💧")
elif page == "Soil Conductivity":
    plot_metric(df_soil, "field3", "Soil Conductivity", "µS/cm", unit="µS/cm", icon="🧲")
elif page == "Soil pH":
    plot_metric(df_soil, "field4", "Soil pH", "pH", unit="pH", icon="🧪")
elif page == "Soil N concentration":
    plot_metric(df_soil, "field5", "Soil N concentration", "mg/kg", unit="mg/kg", icon="🧬")
elif page == "Soil P concentration":
    plot_metric(df_soil, "field6", "Soil P concentration", "mg/kg", unit="mg/kg", icon="🧬")
elif page == "Soil K concentration":
    plot_metric(df_soil, "field7", "Soil K concentration", "mg/kg", unit="mg/kg", icon="🧬")
elif page == "Air Temperature":
    plot_metric(df_env, "field1", "Air Temperature", "°C", unit="°C", icon="🌡️")
elif page == "Air Humidity":
    plot_metric(df_env, "field2", "Air Humidity", "%", unit="%", icon="💦")
elif page == "Luminosity":
    plot_metric(df_env, "field3", "Luminosity", "lux", unit="lux", icon="💡")
elif page == "CO2 concentration":
    plot_metric(df_env, "field4", "CO2 concentration", "ppm", unit="ppm", icon="🟢")
elif page == "Resumen":
    col1, col2, col3, col4 = st.columns(4)
    # Últimos valores
    val_sm, _ = latest_value(df_soil, "field2")   # Soil Moisture
    val_ta, _ = latest_value(df_env,  "field1")   # Air Temp
    val_rh, _ = latest_value(df_env,  "field2")   # Air Humidity
    val_ph, _ = latest_value(df_soil, "field4")   # Soil pH

    # Fondos dinámicos (rojo si fuera de rango)
    bg_sm = _bg_for_main("soil_moist", val_sm)
    bg_ta = _bg_for_main("air_temp",   val_ta)
    bg_rh = _bg_for_main("air_hum",    val_rh)
    bg_ph = _bg_for_main("soil_ph",    val_ph)

    kpi_card(col1, "Soil Moisture", val_sm, unit="%", icon="💧", bg_color=bg_sm)
    kpi_card(col2, "Air Temp",      val_ta, unit="°C", icon="🌡️", bg_color=bg_ta)
    kpi_card(col3, "Air Humidity",  val_rh, unit="%", icon="💦", bg_color=bg_rh)
    kpi_card(col4, "Soil pH",       val_ph, unit="pH", icon="🧪", bg_color=bg_ph)
else:
    st.write("Selecciona una métrica del menú lateral.")
