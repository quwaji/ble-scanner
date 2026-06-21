# BLE Scanner - 仕様書

## 概要

USB BLEドングルを使って周囲のBLEデバイスをスキャンし、
検出履歴をSQLiteに記録するデーモンアプリケーション。
aiohttp製WebUIでブラウザから状況を確認できる。

## 対象環境

- ハードウェア: Raspberry Pi 1 Model B+ (ARMv6, 512MB) / macOS / Windows 10+
- OS: Raspberry Pi OS (Bookworm) / 将来的にYoctoカスタムイメージ
- BLE: USB BLEドングル (KINIVO製 Bluetooth 4.0)
- Python: 3.11+
- 依存ライブラリ: bleak, aiohttp

## ディレクトリ構成

```
ble_scanner/
├── SPEC.md       # この仕様書
├── main.py       # エントリポイント・ログ設定・asyncio起動
├── scanner.py    # BLEスキャンループ
├── db.py         # SQLite操作
├── config.py     # 設定値
└── web.py        # WebUI (aiohttp, port 8080)
```

## スキャン動作

- 30秒ごとに繰り返しスキャン（設定変更可能）
- 1回のスキャン時間: 5秒
- スキャン結果をDBと照合してイベントを生成
- スキャンとWebUIは asyncio.gather で並列実行

## データベース (SQLite)

### devices テーブル

デバイスごとの集約情報。

| カラム | 型 | 説明 |
|---|---|---|
| mac | TEXT PRIMARY KEY | MACアドレス |
| name | TEXT | デバイス名（不明の場合はNULL） |
| first_seen | TEXT | 初回検出日時 (ISO8601) |
| last_seen | TEXT | 最終検出日時 (ISO8601) |
| scan_count | INTEGER | 検出回数 |
| last_rssi | INTEGER | 最終検出時のRSSI (dBm) |
| lost_notified | INTEGER | LOST通知済みフラグ (0/1) |

### scan_events テーブル

スキャンごとの生データ。

| カラム | 型 | 説明 |
|---|---|---|
| id | INTEGER PRIMARY KEY | 自動採番 |
| mac | TEXT | MACアドレス |
| timestamp | TEXT | 検出日時 (ISO8601) |
| rssi | INTEGER | RSSI (dBm) |

### scan_sessions テーブル

スキャン1回ごとの検出台数。ピーク算出に使用。

| カラム | 型 | 説明 |
|---|---|---|
| id | INTEGER PRIMARY KEY | 自動採番 |
| timestamp | TEXT | スキャン実行日時 (ISO8601) |
| device_count | INTEGER | そのスキャンでの検出台数 |

### app_state テーブル

アプリケーション状態のKey-Valueストア。

| key | value |
|---|---|
| session_start | 計測開始日時 (ISO8601)。リセット操作で更新される |

## RSSI 距離感

| RSSI | ラベル | 目安 |
|---|---|---|
| > -60 dBm | near | 数m以内 |
| -60 〜 -80 dBm | medium | 数m〜10m程度 |
| < -80 dBm | far | 10m以上 |

## ログ仕様

標準出力 (stdout) に出力。systemd経由でjournaldに収集される。
WebUIのログパネルにも同時表示される（直近200行）。

```
[NEW]  AA:BB:CC:DD:EE:FF  MyDevice                 -65 dBm  near
[UPD]  AA:BB:CC:DD:EE:FF  MyDevice                 -72 dBm  medium
[LOST] AA:BB:CC:DD:EE:FF  MyDevice                 (last seen: 2026-01-01T12:34:56)
```

- `[NEW]`  : 初回検出デバイス
- `[UPD]`  : 既知デバイスの再検出（LOSTから復帰した場合も [UPD]）
- `[LOST]` : 一定時間（デフォルト10分）検出されなくなったデバイス

> **注意: MAC Address Randomization について**
> スマートフォン等は約15分ごとにBLEアドバタイズ用のMACアドレスをランダムに変更する
> （Resolvable Private Address）。このため、同一の物理デバイスが時間の経過とともに
> 別の `[NEW]` デバイスとして記録され、累積検出デバイス数が実際の物理台数より
> 多くなることがある。固定MACを持つデバイス（センサー類・ビーコン等）は影響を受けない。

## WebUI

- URL: `http://<hostname>:8080/`
- ライブラリ: aiohttp
- 認証: なし（同一ネットワーク内からのアクセスを想定）

### 表示項目

| 項目 | 説明 |
|---|---|
| 計測開始 | session_start の日時 |
| 直近スキャン | 最後のスキャンでの検出台数とその日時 |
| 累積検出デバイス数 | session_start 以降に1回でも検出されたユニークMAC数 |
| ピーク | session_start 以降で最も多かったスキャンの台数と日時 |

### API

| エンドポイント | メソッド | 説明 |
|---|---|---|
| `/` | GET | WebUI HTML |
| `/api/stats` | GET | 統計情報 (JSON) |
| `/api/logs` | GET | 直近ログ最大200行 (JSON) |
| `/api/reset` | POST | session_start を現在時刻に更新 |

### セッションリセット

DBのデータは保持したまま `session_start` のみ更新する。
累積検出数・ピークはリセット後の計測に基づき再集計される。

## 設定値 (config.py / 環境変数)

| 設定名 | 環境変数 | デフォルト | 説明 |
|---|---|---|---|
| SCAN_INTERVAL | BLE_SCAN_INTERVAL | 30秒 | スキャン間隔 |
| SCAN_DURATION | BLE_SCAN_DURATION | 5秒 | 1回のスキャン時間 |
| LOST_THRESHOLD | BLE_LOST_THRESHOLD | 600秒 | LOSTと判定するまでの時間 |
| DB_PATH | BLE_DB_PATH | ~/ble_scanner.db | SQLiteファイルパス |

## systemdサービス

`/etc/systemd/system/ble-scanner.service` として登録済み。
電源投入時に自動起動し、異常終了時は10秒後に自動再起動する。

起動時にBluetoothアダプターのソフトブロック解除 (`rfkill unblock bluetooth`) と
インターフェースのアップ (`hciconfig hci0 up`) を行う。

```bash
sudo systemctl start|stop|restart|status ble-scanner
journalctl -u ble-scanner -f   # ログ確認
```

## インストール手順

```bash
# ファイル転送
scp -r ble_scanner/ pi@raspberrypi.local:~/

# 依存ライブラリ (RPi OS)
pip install bleak aiohttp --break-system-packages

# 依存ライブラリ (macOS / Windows)
pip install bleak aiohttp

# 起動
cd ble_scanner
python main.py
```
