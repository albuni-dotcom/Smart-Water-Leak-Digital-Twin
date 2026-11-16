# Smart Water Leak Detection & Isolation System (Digital Twin)
Mechatronics Engineering - Water Technology Innovation Competition 2026

## Project Structure:
1. `mechanical/model.scad`: High-detail 3D CAD model (OpenSCAD) featuring the 15L gravity tank, 18L collection tank, aluminum 2020 frame, solenoid valves, YF-S201/S401 flow sensors, XGZP6847A pressure sensors with Air Traps, and needle valves.
2. `firmware/`: Real ESP32 C++ firmware (`src/main.cpp`, `platformio.ini`) and Wokwi interactive circuit simulation (`diagram.json`, `wokwi.toml`).
3. `dashboard_ai/app.py`: Bilingual (Arabic/English) real-time physics simulation and leak detection dashboard (Python/Streamlit).
