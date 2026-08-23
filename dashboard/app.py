"""
SmartFlow Dashboard v2.0
Real-time highway toll and traffic monitoring display
"""

import streamlit as st
import sqlite3
import pandas as pd
import time
from datetime import datetime

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

import os
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "smartflow.db")
REFRESH_SECS = 3

st.set_page_config(
    page_title="SmartFlow Dashboard",
    page_icon="🛣️",
    layout="wide"
)

# ══════════════════════════════════════════════════════════════
# DATABASE HELPERS
# ══════════════════════════════════════════════════════════════

def get_conn():
    return sqlite3.connect(DB_PATH)

def query(sql, params=()):
    try:
        conn = get_conn()
        df   = pd.read_sql_query(sql, conn, params=params)
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()

# ══════════════════════════════════════════════════════════════
# DATA FETCHERS
# ══════════════════════════════════════════════════════════════

def get_metrics():
    total_events = query("SELECT COUNT(*) as n FROM events").get("n", [0])[0]
    total_toll   = query("SELECT COALESCE(SUM(toll_inr),0) as t FROM toll_log").get("t", [0])[0]
    cars_on_road = query("""
        SELECT COUNT(*) as n FROM (
            SELECT car_id FROM events WHERE event='ENTRY'
            EXCEPT
            SELECT car_id FROM events WHERE event='EXIT'
        )
    """).get("n", [0])[0]
    latest_state = query("""
        SELECT state FROM traffic_state
        ORDER BY id DESC LIMIT 1
    """)
    state = latest_state["state"].iloc[0] if not latest_state.empty else "FREE"
    return int(total_events), float(total_toll), int(cars_on_road), state

def get_speed_history():
    return query("""
        SELECT ts, speed_kmh FROM events
        WHERE event='SPEED' AND speed_kmh > 0
        ORDER BY id DESC LIMIT 50
    """)

def get_traffic_history():
    return query("""
        SELECT ts, avg_speed, zones_occupied, state
        FROM traffic_state
        ORDER BY id DESC LIMIT 60
    """)

def get_toll_log():
    return query("""
        SELECT car_id, entry_gate, exit_gate,
               distance_km, toll_inr, ts
        FROM toll_log
        ORDER BY id DESC LIMIT 20
    """)

def get_recent_events():
    return query("""
        SELECT ts, event, car_id, speed_kmh, extra
        FROM events
        ORDER BY id DESC LIMIT 15
    """)

def get_zone_history():
    return query("""
        SELECT ts, zones_occupied, state
        FROM traffic_state
        ORDER BY id DESC LIMIT 60
    """)

# ══════════════════════════════════════════════════════════════
# STATE STYLING
# ══════════════════════════════════════════════════════════════

STATE_EMOJI = {
    "FREE":      "🟢",
    "SLOW":      "🟡",
    "CONGESTED": "🟠",
    "JAM":       "🔴"
}

STATE_COLOR = {
    "FREE":      "#00cc44",
    "SLOW":      "#ffcc00",
    "CONGESTED": "#ff8800",
    "JAM":       "#ff2200"
}

# ══════════════════════════════════════════════════════════════
# DASHBOARD LAYOUT
# ══════════════════════════════════════════════════════════════

st.title("🛣️ SmartFlow — Highway Toll & Traffic Monitor")
st.caption(f"Auto-refreshes every {REFRESH_SECS} seconds")

# ── TOP METRICS ───────────────────────────────────────────────

total_events, total_toll, cars_on_road, state = get_metrics()
emoji = STATE_EMOJI.get(state, "⚪")
color = STATE_COLOR.get(state, "#888888")

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("📋 Total Events", total_events)

with col2:
    st.metric("💰 Toll Collected", f"Rs {total_toll:,.2f}")

with col3:
    st.metric("🚗 Cars on Road", f"{cars_on_road} / 4")

with col4:
    st.markdown(f"""
    <div style='background:{color};padding:16px;border-radius:8px;text-align:center'>
        <h3 style='color:white;margin:0'>{emoji} {state}</h3>
        <p style='color:white;margin:0;font-size:12px'>Traffic State</p>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# ── ZONE OCCUPANCY ────────────────────────────────────────────

st.subheader("📍 Live Zone Occupancy")

zone_df = get_zone_history()
if not zone_df.empty:
    latest = zone_df.iloc[0]
    zones  = int(latest["zones_occupied"])

    zcol1, zcol2, zcol3 = st.columns(3)
    zone_labels = ["Zone A (Left curve)", "Zone B (Top straight)", "Zone C (Right curve)"]
    zone_cols   = [zcol1, zcol2, zcol3]

    for idx, (col, label) in enumerate(zip(zone_cols, zone_labels)):
        with col:
            occupied = idx < zones
            bg = "#ff4444" if occupied else "#44aa44"
            status = "🚗 OCCUPIED" if occupied else "✅ CLEAR"
            st.markdown(f"""
            <div style='background:{bg};padding:12px;border-radius:8px;text-align:center'>
                <b style='color:white'>{label}</b><br>
                <span style='color:white'>{status}</span>
            </div>
            """, unsafe_allow_html=True)
else:
    st.info("No zone data yet — waiting for sensors...")

st.divider()

# ── CHARTS ────────────────────────────────────────────────────

chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.subheader("⚡ Speed History")
    speed_df = get_speed_history()
    if not speed_df.empty:
        speed_df = speed_df.sort_values("ts")
        st.line_chart(speed_df.set_index("ts")["speed_kmh"],
                      use_container_width=True)
    else:
        st.info("No speed data yet...")

with chart_col2:
    st.subheader("🚦 Traffic State Distribution")
    traffic_df = get_traffic_history()
    if not traffic_df.empty:
        state_counts = traffic_df["state"].value_counts().reset_index()
        state_counts.columns = ["State", "Count"]
        st.bar_chart(state_counts.set_index("State"),
                     use_container_width=True)
    else:
        st.info("No traffic state data yet...")

st.divider()

# ── ZONE OCCUPANCY HISTORY ────────────────────────────────────

st.subheader("📊 Zone Occupancy Over Time")
if not zone_df.empty:
    zone_plot = zone_df.sort_values("ts")
    st.area_chart(zone_plot.set_index("ts")["zones_occupied"],
                  use_container_width=True)
else:
    st.info("No zone history yet...")

st.divider()

# ── TOLL LOG ──────────────────────────────────────────────────

st.subheader("💳 Recent Toll Transactions")
toll_df = get_toll_log()
if not toll_df.empty:
    toll_df.columns = ["Car ID", "Entry Gate", "Exit Gate",
                        "Distance (km)", "Toll (Rs)", "Time"]
    st.dataframe(toll_df, use_container_width=True, hide_index=True)
else:
    st.info("No toll transactions yet...")

st.divider()

# ── RECENT EVENTS ─────────────────────────────────────────────

st.subheader("📜 Recent Events")
events_df = get_recent_events()
if not events_df.empty:
    events_df.columns = ["Time", "Event", "Car ID", "Speed (km/h)", "Extra"]
    st.dataframe(events_df, use_container_width=True, hide_index=True)
else:
    st.info("No events yet...")

# ── FOOTER ────────────────────────────────────────────────────

st.divider()
st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')} | SmartFlow v2.0")

# Auto refresh
time.sleep(REFRESH_SECS)
st.rerun()