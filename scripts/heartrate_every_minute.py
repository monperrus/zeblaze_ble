#!/usr/bin/env python3
"""Print one live Zeblaze heart-rate value per interval without reconnecting.

The process holds one GATT connection for its entire lifetime. It enables the
watch's real-time push once, then keeps acknowledging every transport message
so the watch can continue sending reports. Stop it with Ctrl-C.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from zeblaze_ble import protocol
from zeblaze_ble.gatttool_transport import GatttoolSession, is_real_time_data_report


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(description="Persistent Zeblaze live heart-rate monitor")
    command_parser.add_argument("address", help="known BLE address of the watch")
    command_parser.add_argument(
        "--interval",
        type=float,
        default=60,
        help="seconds between printed readings (default: 60)",
    )
    command_parser.add_argument(
        "--attempts",
        type=int,
        default=3,
        help="initial connection attempts before giving up (default: 3)",
    )
    command_parser.add_argument("--i-understand-this-writes", action="store_true", required=True)
    return command_parser


async def monitor_once(address: str, interval: float) -> None:
    if interval <= 0:
        raise ValueError("interval must be positive")

    latest_heart_rate: int | None = None
    measured_at_unix: float | None = None
    loop = asyncio.get_running_loop()
    next_output = loop.time() + interval

    async with GatttoolSession(address) as session:
        await session.send_message(protocol.encode_real_time_data_switch_request(True))
        while True:
            try:
                payload = await asyncio.wait_for(
                    session.receive_message(), timeout=max(next_output - loop.time(), 0.1)
                )
            except TimeoutError:
                # `GatttoolSession.receive_message()` has its own short
                # packet timeout. That means a quiet stream can wake this
                # loop before the requested output interval has elapsed.
                # Only emit when the actual cadence deadline is due.
                if loop.time() < next_output:
                    continue
                print(
                    json.dumps(
                        {
                            "reported_at_unix": time.time(),
                            "heart_rate_bpm": latest_heart_rate,
                            "measured_at_unix": measured_at_unix,
                        }
                    ),
                    flush=True,
                )
                next_output = loop.time() + interval
                continue

            if not is_real_time_data_report(payload):
                continue
            reading = protocol.parse_real_time_data(payload)
            if reading.heart_rate > 0:
                first_measurement = latest_heart_rate is None
                latest_heart_rate = reading.heart_rate
                measured_at_unix = time.time()
                if first_measurement:
                    print(
                        json.dumps(
                            {
                                "reported_at_unix": measured_at_unix,
                                "heart_rate_bpm": latest_heart_rate,
                                "measured_at_unix": measured_at_unix,
                            }
                        ),
                        flush=True,
                    )
                    next_output = loop.time() + interval


async def monitor(address: str, interval: float, attempts: int) -> None:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            await monitor_once(address, interval)
            return
        except (ConnectionError, RuntimeError, TimeoutError) as error:
            last_error = error
    raise ConnectionError(
        f"Unable to start the heart-rate monitor after {attempts} attempt(s). "
        "Make sure the watch is awake, nearby, and not connected to another BLE central."
    ) from last_error


def main() -> None:
    arguments = parser().parse_args()
    try:
        asyncio.run(monitor(arguments.address, arguments.interval, arguments.attempts))
    except KeyboardInterrupt:
        pass
    except (ConnectionError, RuntimeError, TimeoutError, ValueError) as error:
        detail = str(error) or "the watch did not accept or maintain a BLE connection"
        raise SystemExit(f"Bluetooth operation failed: {detail}")


if __name__ == "__main__":
    main()
