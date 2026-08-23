"""
SmartFlow Backend v2.0
Handles: ENTRY, EXIT, SPEED, TRAFFIC events from Arduino
Uses real sensor data for ML features instead of synthetic estimates
"""

import serial
import sqlite3
import time
import joblib
import os
import pandas as pd
import numpy as np
from datetime import datetime
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report

# ══════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════
import os
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "smartflow.db")
SERIAL_PORT = "COM3"
BAUD_RATE   = 115200
MODEL_PATH  = "model/rf_traffic.pkl"
TOLL_PER_KM = 2.5

LABELS    = {0: "FREE", 1: "SLOW", 2: "CONGESTED", 3: "JAM"}
LABEL_INV = {v: k for k, v in LABELS.items()}

FEATURE_COLS = ["avg_speed", "speed_std", "vehicle_count",
                "stopped_ratio", "zones_occupied"]

# Rolling window
WINDOW_SECS   = 8
recent_speeds = []   # (timestamp, speed)

# Active vehicles
active_vehicles = {}  # car_id → {entry_gate, entry_time}

# Zone occupancy (from TRAFFIC events)
current_zones = 0
current_state = "FREE"

# ══════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT,
            event       TEXT,
            car_id      INTEGER,
            gate_sensor INTEGER,
            speed_kmh   REAL,
            extra       TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS toll_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            car_id      INTEGER,
            entry_gate  TEXT,
            exit_gate   TEXT,
            distance_km REAL,
            toll_inr    REAL,
            ts          TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS traffic_state (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             TEXT,
            avg_speed      REAL,
            speed_std      REAL,
            vehicle_count  INTEGER,
            stopped_ratio  REAL,
            zones_occupied INTEGER,
            state          TEXT,
            source         TEXT
        )
    """)

    conn.commit()
    conn.close()
    print("[DB] Database initialized")

def db_insert(table, row):
    conn = sqlite3.connect(DB_PATH)
    cols = ",".join(row.keys())
    vals = ",".join(["?"] * len(row))
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({vals})",
                 list(row.values()))
    conn.commit()
    conn.close()

# ══════════════════════════════════════════════════════════════
# MACHINE LEARNING MODEL
# ══════════════════════════════════════════════════════════════

def generate_synthetic_data(n=3000):
    """Synthetic training data — now includes zones_occupied feature"""
    np.random.seed(42)
    rows = []

    # FREE (4-8 km/h, 0 zones)
    n0 = n // 4
    rows.append(pd.DataFrame({
        "avg_speed":      np.random.uniform(4.0, 8.0, n0),
        "speed_std":      np.random.uniform(0.2, 1.0, n0),
        "vehicle_count":  np.random.randint(1, 3, n0),
        "stopped_ratio":  np.random.uniform(0, 0.05, n0),
        "zones_occupied": np.random.randint(0, 2, n0),
        "label": 0
    }))

    # SLOW (2-4 km/h, 1 zone)
    n1 = n // 4
    rows.append(pd.DataFrame({
        "avg_speed":      np.random.uniform(2.0, 4.0, n1),
        "speed_std":      np.random.uniform(0.5, 1.5, n1),
        "vehicle_count":  np.random.randint(2, 4, n1),
        "stopped_ratio":  np.random.uniform(0, 0.15, n1),
        "zones_occupied": np.random.randint(1, 2, n1),
        "label": 1
    }))

    # CONGESTED (0.5-2 km/h, 2 zones)
    n2 = n // 4
    rows.append(pd.DataFrame({
        "avg_speed":      np.random.uniform(0.5, 2.0, n2),
        "speed_std":      np.random.uniform(0.2, 1.0, n2),
        "vehicle_count":  np.random.randint(3, 4, n2),
        "stopped_ratio":  np.random.uniform(0.1, 0.4, n2),
        "zones_occupied": np.random.randint(2, 3, n2),
        "label": 2
    }))

    # JAM (0-0.5 km/h, 3 zones)
    n3 = n - 3 * (n // 4)
    rows.append(pd.DataFrame({
        "avg_speed":      np.random.uniform(0, 0.5, n3),
        "speed_std":      np.random.uniform(0, 0.3, n3),
        "vehicle_count":  np.random.randint(3, 4, n3),
        "stopped_ratio":  np.random.uniform(0.6, 1.0, n3),
        "zones_occupied": np.full(n3, 3),
        "label": 3
    }))

    return pd.concat(rows, ignore_index=True)

def train_model():
    os.makedirs("model", exist_ok=True)
    print("[ML] Training Random Forest classifier...")
    df = generate_synthetic_data(3000)
    X  = df[FEATURE_COLS]
    y  = df["label"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    clf = RandomForestClassifier(
        n_estimators=150, max_depth=10,
        min_samples_leaf=4, random_state=42,
        class_weight="balanced"
    )
    clf.fit(X_train, y_train)
    print("[ML] Training complete.")
    report = classification_report(
    y_test, clf.predict(X_test),
    target_names=list(LABELS.values()),
    output_dict=False
)
    print(f"\n{str(report)}")

    joblib.dump(clf, MODEL_PATH)
    print(f"[ML] Model saved → {MODEL_PATH}")
    return clf

def load_or_train_model():
    if os.path.exists(MODEL_PATH):
        print("[ML] Loading saved model...")
        return joblib.load(MODEL_PATH)
    return train_model()

# ══════════════════════════════════════════════════════════════
# FEATURE EXTRACTION
# ══════════════════════════════════════════════════════════════

def extract_features(vehicle_count, zones_occupied):
    """Extract ML features from rolling window + real zone data"""
    global recent_speeds
    now = time.time()

    # Prune old data
    recent_speeds = [(t, s) for t, s in recent_speeds
                     if now - t < WINDOW_SECS]

    if len(recent_speeds) < 2:
        return None

    speeds = [s for _, s in recent_speeds]
    avg_speed     = float(np.mean(speeds))
    speed_std     = float(np.std(speeds))
    stopped_ratio = sum(1 for s in speeds if s < 0.3) / len(speeds)

    return {
        "avg_speed":      avg_speed,
        "speed_std":      speed_std,
        "vehicle_count":  vehicle_count,
        "stopped_ratio":  stopped_ratio,
        "zones_occupied": zones_occupied,  # real sensor data!
    }

def classify_traffic(clf, vehicle_count, zones_occupied):
    """ML classification using real zone occupancy data"""
    feats = extract_features(vehicle_count, zones_occupied)

    if feats is None:
        return "FREE", 1.0, None

    X_pred = pd.DataFrame([feats])[FEATURE_COLS]
    pred   = clf.predict(X_pred)[0]
    proba  = clf.predict_proba(X_pred)[0]
    state  = LABELS[pred]

    # Consensus filter
    if pred >= 2 and len(recent_speeds) < 5:
        state = "SLOW"
        print("  [Filter] Not enough data → downgraded to SLOW")

    db_insert("traffic_state", {
        "ts":             datetime.now().isoformat(),
        "avg_speed":      round(feats["avg_speed"], 2),
        "speed_std":      round(feats["speed_std"], 2),
        "vehicle_count":  vehicle_count,
        "stopped_ratio":  round(feats["stopped_ratio"], 2),
        "zones_occupied": zones_occupied,
        "state":          state,
        "source":         "ML"
    })

    return state, float(max(proba)), feats

# ══════════════════════════════════════════════════════════════
# SERIAL LINE PARSER
# ══════════════════════════════════════════════════════════════

def parse_line(line, clf):
    global current_zones, current_state

    # Skip debug lines
    if line.startswith("#"):
        return

    parts = line.strip().split(",")
    if len(parts) < 6:
        return

    event, car_id, gate_sensor, speed_kmh, extra, ts_ms = parts

    try:
        car_id    = int(car_id)
        speed_kmh = float(speed_kmh)
    except ValueError:
        return

    # Log every event to database
    db_insert("events", {
        "ts":          datetime.now().isoformat(),
        "event":       event,
        "car_id":      car_id,
        "gate_sensor": int(gate_sensor),
        "speed_kmh":   speed_kmh,
        "extra":       extra
    })

    # ── ENTRY ─────────────────────────────────────────────────
    if event == "ENTRY":
        active_vehicles[car_id] = {
            "entry_gate": extra,
            "entry_time": datetime.now()
        }
        print(f"\n[ENTRY] Car {car_id} entered at {extra}")
        print(f"        Active vehicles: {len(active_vehicles)}")

    # ── EXIT ──────────────────────────────────────────────────
    elif event == "EXIT":
        tokens    = extra.split(":")
        exit_gate = tokens[0]
        dist_km   = float(tokens[1].replace("km", ""))
        toll      = float(tokens[2].replace("Rs", ""))

        entry_info = active_vehicles.pop(car_id, {})
        entry_gate = entry_info.get("entry_gate", "?")

        db_insert("toll_log", {
            "car_id":      car_id,
            "entry_gate":  entry_gate,
            "exit_gate":   exit_gate,
            "distance_km": round(dist_km, 2),
            "toll_inr":    round(toll, 2),
            "ts":          datetime.now().isoformat()
        })

        print(f"\n[EXIT]  Car {car_id} | {entry_gate} → {exit_gate}")
        print(f"        Distance: {dist_km:.1f} km")
        print(f"        Toll:     Rs {toll:.2f}")
        print(f"        Speed:    {speed_kmh:.1f} km/h")
        print(f"        Active:   {len(active_vehicles)}")

    # ── SPEED ─────────────────────────────────────────────────
    elif event == "SPEED":
        if speed_kmh > 0:
            recent_speeds.append((time.time(), speed_kmh))

            state, conf, feats = classify_traffic(
                clf, len(active_vehicles), current_zones
            )
            current_state = state

            print(f"\n[SPEED] Sensor {gate_sensor}: {speed_kmh:.2f} km/h")
            if feats:
                print(f"  [ML INPUT]  avg={feats['avg_speed']:.2f} "
                      f"std={feats['speed_std']:.2f} "
                      f"zones={feats['zones_occupied']} "
                      f"stopped={feats['stopped_ratio']:.2f}")
            else:
                print(f"  [ML INPUT]  Warming up...")
            print(f"  [ML OUTPUT] → {state} (confidence: {conf*100:.0f}%)")

            if state in ("CONGESTED", "JAM"):
                print(f"  *** ALERT: {state} detected! ***")

    # ── TRAFFIC ───────────────────────────────────────────────
    elif event == "TRAFFIC":
        tokens         = extra.split(":")
        hw_state       = tokens[0]   # state from Arduino
        current_zones  = int(gate_sensor)  # zones occupied

        # Also run ML classification with real zone data
        if len(recent_speeds) >= 2:
            state, conf, feats = classify_traffic(
                clf, len(active_vehicles), current_zones
            )
            current_state = state
            print(f"\n[TRAFFIC] Zones: {current_zones}/3 | "
                  f"HW: {hw_state} | ML: {state} ({conf*100:.0f}%)")
        else:
            # Use hardware state if no speed data yet
            current_state = hw_state
            db_insert("traffic_state", {
                "ts":             datetime.now().isoformat(),
                "avg_speed":      speed_kmh,
                "speed_std":      0,
                "vehicle_count":  len(active_vehicles),
                "stopped_ratio":  current_zones / 3.0,
                "zones_occupied": current_zones,
                "state":          hw_state,
                "source":         "HW"
            })
            print(f"\n[TRAFFIC] Zones: {current_zones}/3 | "
                  f"State: {hw_state} (hardware)")

# ══════════════════════════════════════════════════════════════
# SIMULATION MODE
# ══════════════════════════════════════════════════════════════

def run_simulation(clf):
    print("\n[SIM] Running simulation with 4 toy cars...")
    print("[SIM] Scenario: Free → Congestion → JAM → Recovery\n")

    events = [
        # Entries
        ("ENTRY",   1, 3, 0,    "IE1"),
        ("ENTRY",   2, 4, 0,    "IE2"),
        ("ENTRY",   3, 5, 0,    "IE3"),
        ("ENTRY",   4, 3, 0,    "IE1"),

        # Free flow — 0 zones occupied
        ("TRAFFIC", 0, 0, 6.5,  "FREE:0"),
        ("SPEED",   0, 1, 6.5,  "us0-us1"),
        ("SPEED",   0, 2, 6.0,  "us1-us2"),
        ("TRAFFIC", 0, 1, 6.0,  "SLOW:1"),
        ("SPEED",   0, 1, 5.8,  "us0-us1"),

        # Congestion — 2 zones occupied
        ("TRAFFIC", 0, 2, 2.5,  "CONGESTED:2"),
        ("SPEED",   0, 1, 2.5,  "us0-us1"),
        ("SPEED",   0, 2, 2.0,  "us1-us2"),
        ("TRAFFIC", 0, 2, 1.5,  "CONGESTED:2"),

        # JAM — 3 zones occupied
        ("TRAFFIC", 0, 3, 0.3,  "JAM:3"),
        ("SPEED",   0, 1, 0.3,  "us0-us1"),
        ("SPEED",   0, 2, 0.2,  "us1-us2"),
        ("TRAFFIC", 0, 3, 0.1,  "JAM:3"),

        # Exits
        ("EXIT",    1, 0, 5.5,  "OE1:100.0km:Rs250.00"),
        ("EXIT",    2, 1, 4.8,  "OE2:160.0km:Rs400.00"),

        # Recovery
        ("TRAFFIC", 0, 1, 4.0,  "SLOW:1"),
        ("SPEED",   0, 1, 4.5,  "us0-us1"),
        ("TRAFFIC", 0, 0, 6.0,  "FREE:0"),
        ("SPEED",   0, 1, 6.2,  "us0-us1"),

        # Last exits
        ("EXIT",    3, 2, 5.0,  "OE3:200.0km:Rs500.00"),
        ("EXIT",    4, 0, 6.0,  "OE1:100.0km:Rs250.00"),
    ]

    for ev in events:
        event, car_id, gate, speed, extra = ev
        line = f"{event},{car_id},{gate},{speed},{extra},{int(time.time()*1000)}"
        parse_line(line, clf)
        time.sleep(0.6)

    print("\n[SIM] Simulation complete")

# ══════════════════════════════════════════════════════════════
# SERIAL LOOP
# ══════════════════════════════════════════════════════════════

def serial_loop(clf):
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
        print(f"[Serial] Connected on {SERIAL_PORT}")
        ser.readline()  # skip header
        print("[Serial] Listening for events...\n")

        while True:
            try:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if line and not line.startswith("event") \
                        and not line.startswith("SmartFlow"):
                    parse_line(line, clf)
            except Exception as e:
                print(f"[Serial] Parse error: {e}")

    except Exception as e:
        print(f"[Serial] Could not connect: {e}")
        print("[Serial] Switching to SIMULATION mode\n")
        run_simulation(clf)

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    init_db()
    clf = load_or_train_model()
    serial_loop(clf)