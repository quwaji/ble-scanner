import os
from pathlib import Path

SCAN_INTERVAL = int(os.getenv("BLE_SCAN_INTERVAL", "30"))   # seconds
SCAN_DURATION = float(os.getenv("BLE_SCAN_DURATION", "5"))  # seconds
LOST_THRESHOLD = int(os.getenv("BLE_LOST_THRESHOLD", "600"))  # seconds
DB_PATH = Path(os.getenv("BLE_DB_PATH", Path.home() / "ble_scanner.db"))
