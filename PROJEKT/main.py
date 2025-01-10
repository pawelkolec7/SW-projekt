import sqlite3
import time
from datetime import datetime
from flask import Flask, render_template, request, jsonify
import RPi.GPIO as GPIO
import Adafruit_DHT
import smbus
from threading import Thread

# --- Konfiguracja czujników ---
DHT_SENSOR = Adafruit_DHT.DHT11
DHT_PIN = 4

DEVICE = 0x23
ONE_TIME_HIGH_RES_MODE = 0x20
bus = smbus.SMBus(1)

SOIL_SENSOR_PIN = 17
BUTTON_CONTROL_PIN = 22
PUMP_PIN = 27

GPIO.setmode(GPIO.BCM)
GPIO.setup(SOIL_SENSOR_PIN, GPIO.IN)
GPIO.setup(BUTTON_CONTROL_PIN, GPIO.OUT)
GPIO.setup(PUMP_PIN, GPIO.OUT)
GPIO.output(BUTTON_CONTROL_PIN, GPIO.LOW)
GPIO.output(PUMP_PIN, GPIO.LOW)

# --- Flask ---
app = Flask(__name__)

# --- SQLite ---
DATABASE = "sensor_data.db"

def init_db():
    """Tworzy bazę danych, jeśli jeszcze nie istnieje."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            temperature REAL,
            humidity REAL,
            light_level REAL
        )
    """)
    conn.commit()
    conn.close()

def insert_data(temperature, humidity, light_level):
    """Zapisuje dane do bazy."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sensor_data (temperature, humidity, light_level)
        VALUES (?, ?, ?)
    """, (temperature, humidity, light_level))
    conn.commit()
    conn.close()

def get_data(limit=100):
    """Pobiera ostatnie dane z bazy."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, temperature, humidity, light_level
        FROM sensor_data
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,))
    data = cursor.fetchall()
    conn.close()
    return data

def get_latest_data():
    """Pobiera najnowsze dane z bazy."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, temperature, humidity, light_level
        FROM sensor_data
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    data = cursor.fetchone()
    conn.close()
    return data

# --- Funkcje pomocnicze ---
def read_sensors():
    humidity, temperature = Adafruit_DHT.read_retry(DHT_SENSOR, DHT_PIN)
    try:
        data = bus.read_i2c_block_data(DEVICE, ONE_TIME_HIGH_RES_MODE)
        light_level = (data[0] << 8) | data[1]
    except:
        light_level = None
    return temperature, humidity, light_level

# Funkcja do uśredniania danych po usunięciu skrajnych wartości
def calculate_average(data):
    if len(data) > 2:  # Minimum 3 wartości, żeby usunąć skrajne
        data.remove(max(data))
        data.remove(min(data))
    return sum(data) / len(data) if data else None

# --- Aktualizacja danych w tle ---
def background_task():
    while True:
        temperature_readings = []
        humidity_readings = []
        light_level_readings = []

        for _ in range(10):  # Pobieramy 10 odczytów co minutę (co 6 sekund)
            temperature, humidity, light_level = read_sensors()
            
            # Dodajemy tylko prawidłowe wartości
            if temperature is not None:
                temperature_readings.append(temperature)
            if humidity is not None:
                humidity_readings.append(humidity)
            if light_level is not None:
                light_level_readings.append(light_level)

            time.sleep(6)  # Odczyt co 6 sekund

        # Usuwanie wartości skrajnych i obliczanie średniej
        avg_temperature = calculate_average(temperature_readings)
        avg_humidity = calculate_average(humidity_readings)
        avg_light_level = calculate_average(light_level_readings)

        # Wstawienie uśrednionych danych do bazy
        insert_data(avg_temperature, avg_humidity, avg_light_level)

        time.sleep(240)  # Oczekiwanie przez minutę przed kolejnym pobraniem danych

# --- Funkcja monitorująca wilgotność gleby ---
def monitor_soil_moisture():
    while True:
        if GPIO.input(SOIL_SENSOR_PIN) == GPIO.HIGH:  # Niska wilgotność
            # Przekaż sygnał do portu 22, aby włączyć pompę
            GPIO.output(BUTTON_CONTROL_PIN, GPIO.HIGH)  # Włącz sygnał na porcie 22
            GPIO.output(PUMP_PIN, GPIO.HIGH)  # Włącz pompę
            time.sleep(2)  # Czas działania pompy
            GPIO.output(PUMP_PIN, GPIO.LOW)  # Wyłącz pompę
            GPIO.output(BUTTON_CONTROL_PIN, GPIO.LOW)  # Wyłącz sygnał na porcie 22
        time.sleep(5)  # Sprawdzaj co 5 sekund

# Uruchomienie monitorowania wilgotności gleby w osobnym wątku
Thread(target=monitor_soil_moisture, daemon=True).start()

# --- Trasy ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/latest-data")
def latest_data():
    """API do pobierania najnowszych danych (AJAX)."""
    data = get_latest_data()
    if data:
        timestamp, temperature, humidity, light_level = data
        return jsonify({
            "timestamp": timestamp,
            "temperature": temperature,
            "humidity": humidity,
            "light_level": light_level
        })
    return jsonify({})

@app.route("/charts")
def charts():
    data = get_data(limit=100)
    return render_template("charts.html", data=data)

@app.route("/api/data")
def api_data():
    data = get_data(limit=100)
    return jsonify(data)

@app.route("/api/data-paginated")
def api_data_paginated():
    """API do pobierania danych z obsługą stronicowania."""
    limit = int(request.args.get("limit", 10))  # Domyślnie 100 rekordów na stronę
    offset = int(request.args.get("offset", 0))  # Domyślnie zaczynamy od pierwszego rekordu

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, temperature, humidity, light_level
        FROM sensor_data
        ORDER BY timestamp DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    data = cursor.fetchall()
    conn.close()

    return jsonify(data)
    
@app.route("/api/water", methods=["POST"])
def water_plants():
    """Ręczne podlewanie roślin za pomocą przycisku na stronie."""
    try:
        GPIO.output(PUMP_PIN, GPIO.HIGH)  # Włącz pompę
        GPIO.output(BUTTON_CONTROL_PIN, GPIO.HIGH)  # Włącz sygnał na porcie 22
        time.sleep(2)  # Czas działania pompy
        GPIO.output(PUMP_PIN, GPIO.LOW)  # Wyłącz pompę
        GPIO.output(BUTTON_CONTROL_PIN, GPIO.LOW)  # Wyłącz sygnał na porcie 22
        return jsonify({"message": "Podlewanie zakończone pomyślnie!"}), 200
    except Exception as e:
        return jsonify({"message": f"Błąd podlewania: {str(e)}"}), 500


# --- Inicjalizacja ---
if __name__ == "__main__":
    init_db()
    from threading import Thread
    Thread(target=background_task, daemon=True).start()
    app.run(host="0.0.0.0", port=1234)  
import sqlite3
import time
from datetime import datetime
from flask import Flask, render_template, request, jsonify
import RPi.GPIO as GPIO
import Adafruit_DHT
import smbus
from threading import Thread

# --- Konfiguracja czujników ---
DHT_SENSOR = Adafruit_DHT.DHT11
DHT_PIN = 4

DEVICE = 0x23
ONE_TIME_HIGH_RES_MODE = 0x20
bus = smbus.SMBus(1)

SOIL_SENSOR_PIN = 17
BUTTON_CONTROL_PIN = 22
PUMP_PIN = 27

GPIO.setmode(GPIO.BCM)
GPIO.setup(SOIL_SENSOR_PIN, GPIO.IN)
GPIO.setup(BUTTON_CONTROL_PIN, GPIO.OUT)
GPIO.setup(PUMP_PIN, GPIO.OUT)
GPIO.output(BUTTON_CONTROL_PIN, GPIO.LOW)
GPIO.output(PUMP_PIN, GPIO.LOW)

# --- Flask ---
app = Flask(__name__)

# --- SQLite ---
DATABASE = "sensor_data.db"

def init_db():
    """Tworzy bazę danych, jeśli jeszcze nie istnieje."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            temperature REAL,
            humidity REAL,
            light_level REAL
        )
    """)
    conn.commit()
    conn.close()

def insert_data(temperature, humidity, light_level):
    """Zapisuje dane do bazy."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sensor_data (temperature, humidity, light_level)
        VALUES (?, ?, ?)
    """, (temperature, humidity, light_level))
    conn.commit()
    conn.close()

def get_data(limit=100):
    """Pobiera ostatnie dane z bazy."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, temperature, humidity, light_level
        FROM sensor_data
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,))
    data = cursor.fetchall()
    conn.close()
    return data

def get_latest_data():
    """Pobiera najnowsze dane z bazy."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, temperature, humidity, light_level
        FROM sensor_data
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    data = cursor.fetchone()
    conn.close()
    return data

# --- Funkcje pomocnicze ---
def read_sensors():
    humidity, temperature = Adafruit_DHT.read_retry(DHT_SENSOR, DHT_PIN)
    try:
        data = bus.read_i2c_block_data(DEVICE, ONE_TIME_HIGH_RES_MODE)
        light_level = (data[0] << 8) | data[1]
    except:
        light_level = None
    return temperature, humidity, light_level

# Funkcja do uśredniania danych po usunięciu skrajnych wartości
def calculate_average(data):
    if len(data) > 2:  # Minimum 3 wartości, żeby usunąć skrajne
        data.remove(max(data))
        data.remove(min(data))
    return sum(data) / len(data) if data else None

# --- Aktualizacja danych w tle ---
def background_task():
    while True:
        temperature_readings = []
        humidity_readings = []
        light_level_readings = []

        for _ in range(10):  # Pobieramy 10 odczytów co minutę (co 6 sekund)
            temperature, humidity, light_level = read_sensors()
            
            # Dodajemy tylko prawidłowe wartości
            if temperature is not None:
                temperature_readings.append(temperature)
            if humidity is not None:
                humidity_readings.append(humidity)
            if light_level is not None:
                light_level_readings.append(light_level)

            time.sleep(6)  # Odczyt co 6 sekund

        # Usuwanie wartości skrajnych i obliczanie średniej
        avg_temperature = calculate_average(temperature_readings)
        avg_humidity = calculate_average(humidity_readings)
        avg_light_level = calculate_average(light_level_readings)

        # Wstawienie uśrednionych danych do bazy
        insert_data(avg_temperature, avg_humidity, avg_light_level)

        time.sleep(240)  # Oczekiwanie przez minutę przed kolejnym pobraniem danych

# --- Funkcja monitorująca wilgotność gleby ---
def monitor_soil_moisture():
    while True:
        if GPIO.input(SOIL_SENSOR_PIN) == GPIO.HIGH:  # Niska wilgotność
            # Przekaż sygnał do portu 22, aby włączyć pompę
            GPIO.output(BUTTON_CONTROL_PIN, GPIO.HIGH)  # Włącz sygnał na porcie 22
            GPIO.output(PUMP_PIN, GPIO.HIGH)  # Włącz pompę
            time.sleep(2)  # Czas działania pompy
            GPIO.output(PUMP_PIN, GPIO.LOW)  # Wyłącz pompę
            GPIO.output(BUTTON_CONTROL_PIN, GPIO.LOW)  # Wyłącz sygnał na porcie 22
        time.sleep(5)  # Sprawdzaj co 5 sekund

# Uruchomienie monitorowania wilgotności gleby w osobnym wątku
Thread(target=monitor_soil_moisture, daemon=True).start()

# --- Trasy ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/latest-data")
def latest_data():
    """API do pobierania najnowszych danych (AJAX)."""
    data = get_latest_data()
    if data:
        timestamp, temperature, humidity, light_level = data
        return jsonify({
            "timestamp": timestamp,
            "temperature": temperature,
            "humidity": humidity,
            "light_level": light_level
        })
    return jsonify({})

@app.route("/charts")
def charts():
    data = get_data(limit=100)
    return render_template("charts.html", data=data)

@app.route("/api/data")
def api_data():
    data = get_data(limit=100)
    return jsonify(data)

@app.route("/api/data-paginated")
def api_data_paginated():
    """API do pobierania danych z obsługą stronicowania."""
    limit = int(request.args.get("limit", 10))  # Domyślnie 100 rekordów na stronę
    offset = int(request.args.get("offset", 0))  # Domyślnie zaczynamy od pierwszego rekordu

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, temperature, humidity, light_level
        FROM sensor_data
        ORDER BY timestamp DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    data = cursor.fetchall()
    conn.close()

    return jsonify(data)
    
@app.route("/api/water", methods=["POST"])
def water_plants():
    """Ręczne podlewanie roślin za pomocą przycisku na stronie."""
    try:
        GPIO.output(PUMP_PIN, GPIO.HIGH)  # Włącz pompę
        GPIO.output(BUTTON_CONTROL_PIN, GPIO.HIGH)  # Włącz sygnał na porcie 22
        time.sleep(2)  # Czas działania pompy
        GPIO.output(PUMP_PIN, GPIO.LOW)  # Wyłącz pompę
        GPIO.output(BUTTON_CONTROL_PIN, GPIO.LOW)  # Wyłącz sygnał na porcie 22
        return jsonify({"message": "Podlewanie zakończone pomyślnie!"}), 200
    except Exception as e:
        return jsonify({"message": f"Błąd podlewania: {str(e)}"}), 500


# --- Inicjalizacja ---
if __name__ == "__main__":
    init_db()
    from threading import Thread
    Thread(target=background_task, daemon=True).start()
    app.run(host="0.0.0.0", port=1234)  

