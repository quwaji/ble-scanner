import asyncio
import logging
import sqlite3

from bleak import BleakScanner
from bleak.assigned_numbers import AdvertisementDataType
from bleak.backends.bluezdbus.advertisement_monitor import OrPattern
from bleak.backends.bluezdbus.scanner import BlueZScannerArgs
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

import config
import db

logger = logging.getLogger(__name__)

# Passive scanning needs at least one advertisement-monitor pattern. Matching on
# the common Flags values catches the large majority of advertising BLE devices
# (phones, wearables, beacons, TVs) without the active-discovery start/stop churn
# that wedges the adapter on this hardware. Devices that omit the Flags AD
# structure entirely won't match; add manufacturer/service-data patterns here if
# any expected device is missing.
_PASSIVE_PATTERNS = [
    OrPattern(0, AdvertisementDataType.FLAGS, bytes([flags]))
    for flags in (0x02, 0x04, 0x05, 0x06, 0x18, 0x1a, 0x1b)
]


def rssi_label(rssi: int | None) -> str:
    if rssi is None:
        return "unknown"
    if rssi > -60:
        return "near"
    if rssi > -80:
        return "medium"
    return "far"


def log_results(detected: dict[str, tuple[str | None, int | None]], db_results: dict[str, bool]) -> None:
    """Log scan summary."""
    for mac, (name, rssi) in sorted(detected.items()):
        is_new = db_results[mac]
        tag = "[NEW]" if is_new else "[UPD]"
        display_name = name or "(unknown)"
        rssi_str = f"{rssi:4d} dBm" if rssi is not None else "  -- dBm"
        label = rssi_label(rssi)
        logger.info("%-6s %-17s  %-24s  %s  %s", tag, mac, display_name, rssi_str, label)


def _record_window(conn: sqlite3.Connection, window: dict[str, tuple[str | None, int | None]]) -> None:
    # Single batched transaction: committing per device let recording outrun the
    # scan interval on slow hardware. See db.record_window.
    new_macs, lost = db.record_window(conn, window, config.LOST_THRESHOLD)

    db_results = {mac: (mac in new_macs) for mac in window}
    logger.info("--- Found %d device(s) ---", len(window))
    log_results(window, db_results)

    for row in lost:
        display_name = row["name"] or "(unknown)"
        logger.info("[LOST] %-17s  %-24s  (last seen: %s)", row["mac"], display_name, row["last_seen"])


async def scan_loop(conn: sqlite3.Connection) -> None:
    logger.info("BLE scanner started (passive). interval=%ds lost_threshold=%ds",
                config.SCAN_INTERVAL, config.LOST_THRESHOLD)

    # Best RSSI/name seen per MAC during the current interval window. The callback
    # runs on the event loop, so no locking is needed around this dict.
    detected: dict[str, tuple[str | None, int | None]] = {}

    def callback(device: BLEDevice, adv: AdvertisementData) -> None:
        # BlueZ reports -127 dBm (and sometimes None) when it has no valid RSSI
        # for an advertisement, typically for cached devices that are no longer
        # nearby. Skip these so the count reflects devices actually in range.
        if adv.rssi is None or adv.rssi <= -127:
            return
        mac = device.address.upper()
        name = adv.local_name or device.name or None
        existing_rssi = detected.get(mac, (None, None))[1]
        if existing_rssi is None or adv.rssi > existing_rssi:
            detected[mac] = (name, adv.rssi)

    # One passive monitor, started once and kept running. Unlike active discovery,
    # passive scanning re-delivers advertisements continuously, so the callback
    # keeps firing and each window's snapshot reflects the devices present then.
    scanner = BleakScanner(
        detection_callback=callback,
        scanning_mode="passive",
        bluez=BlueZScannerArgs(or_patterns=_PASSIVE_PATTERNS),
    )
    try:
        await scanner.start()
    except BleakError as e:
        logger.error(
            "Passive scan start failed (needs bleak>=0.19 and BlueZ "
            "'Experimental = true'): %s", e,
        )
        logger.error("Scanning disabled; web UI stays up.")
        return

    logger.info("--- Passive monitoring started, sampling every %ds ---", config.SCAN_INTERVAL)
    try:
        while True:
            await asyncio.sleep(config.SCAN_INTERVAL)
            window = dict(detected)
            detected.clear()
            _record_window(conn, window)
    finally:
        try:
            await scanner.stop()
        except BleakError as e:
            logger.warning("scanner.stop() failed (ignored): %s", e)
