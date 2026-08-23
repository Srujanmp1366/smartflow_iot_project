
# 🛣️ SmartFlow — IoT Dynamic Toll & Traffic Monitor

An IoT-based highway simulation system with real-time traffic 
monitoring and dynamic toll calculation using Machine Learning.

## Hardware
- Arduino Mega 2560
- 6× FC-51 IR sensors (entry/exit gates)
- 3× HC-SR04 ultrasonic sensors (speed + zone occupancy)
- Green/Yellow/Red LEDs + buzzer (traffic state indicators)
- 80×50cm track board with toy cars

## Software Stack
- **Arduino firmware** — C++ (Arduino IDE)
- **Python backend** — pyserial, scikit-learn, pandas, SQLite
- **Dashboard** — Streamlit

## Project Structure
smartflow/
├── hub_firmware/
│ └── src/
│ └── main.cpp ← Arduino firmware
├── backend/
│ └── smartflow_backend.py ← Python ML backend
├── dashboard/
│ └── app.py ← Streamlit dashboard
└── README.md


## How to Run

### 1. Upload Arduino firmware
Open `hub_firmware/src/main.cpp` in Arduino IDE,
select Arduino Mega 2560 + correct COM port, upload.

### 2. Install Python dependencies
```bash
python -m venv venv
venv\Scripts\activate
pip install pyserial scikit-learn pandas numpy joblib streamlit
```

### 3. Run backend
```bash
python backend/smartflow_backend.py
```

### 4. Run dashboard
```bash
streamlit run dashboard/app.py
```
Open http://localhost:8501

## Features
- Dynamic toll: max(zone distance, lap distance, speed×time)
- ML traffic classification: FREE / SLOW / CONGESTED / JAM
- Real-time zone occupancy from ultrasonic sensors
- Live Streamlit dashboard with auto-refresh
- Simulation mode when Arduino not connected
