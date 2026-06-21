import asyncio
import logging
import sqlite3
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

import config
import db

logger = logging.getLogger(__name__)


def rssi_label(rssi: int | None) -> str:
    if rssi is None:
        return "unknown"
    if rssi > -60:
        return "near"
    if rssi > -80:
        return "medium"
    return "far"


async def run_scan() -> dict[str, tuple[str | None, int | None]]:
    """Scan for SCAN_DURATION seconds. Returns {mac: (name, best_rssi)}."""
    detected: dict[str, tuple[str | None, int | None]] = {}

    def callback(device: BLEDevice, adv: AdvertisementData) -> None:
        mac = device.address.upper()
        existing_rssi = detected.get(mac, (None, None))[1]
        if existing_rssi is None or (adv.rssi is not None and adv.rssi > existing_rssi):
            detected[mac] = (device.name or None, adv.rssi)

    async with BleakScanner(detection_callback=callback):
        await asyncio.sleep(config.SCAN_DURATION)

    return detected


def log_results(detected: dict[str, tuple[str | None, int | None]], db_results: dict[str, bool]) -> None:
    """Log scan summary."""
    for mac, (name, rssi) in sorted(detected.items()):
        is_new = db_results[mac]
        tag = "[NEW]" if is_new else "[UPD]"
        display_name = name or "(unknown)"
        rssi_str = f"{rssi:4d} dBm" if rssi is not None else "  -- dBm"
        label = rssi_label(rssi)
        logger.info("%-6s %-17s  %-24s  %s  %s", tag, mac, display_name, rssi_str, label)


async def scan_loop(conn: sqlite3.Connection) -> None:
    logger.info("BLE scanner started. interval=%ds duration=%ds lost_threshold=%ds",
                config.SCAN_INTERVAL, int(config.SCAN_DURATION), config.LOST_THRESHOLD)
    while True:
        logger.info("--- Scanning for %.0f seconds ---", config.SCAN_DURATION)
        detected = await run_scan()

        db_results: dict[str, bool] = {}
        for mac, (name, rssi) in detected.items():
            is_new = db.upsert_device(conn, mac, name, rssi)
            db.record_scan_event(conn, mac, rssi)
            db_results[mac] = is_new

        logger.info("--- Found %d device(s) ---", len(detected))
        log_results(detected, db_results)
        db.record_scan_session(conn, len(detected))

        lost = db.get_newly_lost_devices(conn, config.LOST_THRESHOLD)
        for row in lost:
            display_name = row["name"] or "(unknown)"
            logger.info("[LOST] %-17s  %-24s  (last seen: %s)", row["mac"], display_name, row["last_seen"])
            db.mark_lost_notified(conn, row["mac"])

        logger.info("--- Next scan in %ds ---", config.SCAN_INTERVAL)
        await asyncio.sleep(config.SCAN_INTERVAL)
