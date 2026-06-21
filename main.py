import asyncio
import logging
import sys

import config
import db
import scanner
import web as webui


def setup_logging() -> None:
    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    wh = webui.WebLogHandler()
    wh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(sh)
    root.addHandler(wh)


def main() -> None:
    setup_logging()
    log = logging.getLogger(__name__)
    conn = db.connect(config.DB_PATH)
    try:
        asyncio.run(_run(conn))
    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        conn.close()


async def _run(conn) -> None:
    await asyncio.gather(
        webui.run_server(conn),
        scanner.scan_loop(conn),
    )


if __name__ == "__main__":
    main()
