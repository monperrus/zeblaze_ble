"""Bleak-based, read-only discovery and notification capture."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from bleak import BleakClient, BleakScanner

from .protocol import NOTIFICATION_CHANNELS, ZH_SDK_SERVICE


@dataclass(frozen=True)
class WatchAdvertisement:
    address: str
    name: str | None
    rssi: int | None
    service_uuids: list[str]


async def scan(timeout: float) -> list[WatchAdvertisement]:
    """Return advertisements carrying the ZH_SDK service."""
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    matches: list[WatchAdvertisement] = []
    for device, advertisement in devices.values():
        service_uuids = [uuid.lower() for uuid in (advertisement.service_uuids or [])]
        name = device.name or advertisement.local_name
        if ZH_SDK_SERVICE in service_uuids or (name and name.lower().startswith("beyond 3 pro_")):
            matches.append(
                WatchAdvertisement(
                    address=device.address,
                    name=name,
                    rssi=advertisement.rssi,
                    service_uuids=service_uuids,
                )
            )
    return matches


async def inspect(address: str) -> dict[str, object]:
    """Discover and return the watch's GATT table without writing anything."""
    async with BleakClient(address) as client:
        services = client.services
        return {
            "address": address,
            "connected": client.is_connected,
            "services": [
                {
                    "uuid": service.uuid,
                    "characteristics": [
                        {"uuid": characteristic.uuid, "properties": sorted(characteristic.properties)}
                        for characteristic in service.characteristics
                    ],
                }
                for service in services
            ],
        }


async def listen(address: str, seconds: float, output: Path, on_packet: Callable[[dict[str, object]], None]) -> int:
    """Subscribe to every observed ZH_SDK channel and log notifications."""
    packets = 0
    output.parent.mkdir(parents=True, exist_ok=True)

    async with BleakClient(address) as client, output.open("w", encoding="utf-8") as stream:
        services = client.services
        available = {characteristic.uuid.lower(): characteristic for service in services for characteristic in service.characteristics}
        missing = set(NOTIFICATION_CHANNELS) - set(available)
        if missing:
            raise RuntimeError(f"watch is missing expected ZH_SDK channels: {', '.join(sorted(missing))}")

        def receive(sender: object, data: bytearray) -> None:
            nonlocal packets
            packets += 1
            packet = {
                "timestamp_unix": time.time(),
                "characteristic": str(getattr(sender, "uuid", sender)).lower(),
                "payload_hex": bytes(data).hex(),
            }
            stream.write(json.dumps(packet) + "\n")
            stream.flush()
            on_packet(packet)

        subscribed: list[str] = []
        try:
            for uuid in NOTIFICATION_CHANNELS:
                characteristic = available[uuid]
                if "notify" not in characteristic.properties and "indicate" not in characteristic.properties:
                    continue
                await client.start_notify(characteristic, receive)
                subscribed.append(uuid)
            if not subscribed:
                raise RuntimeError("none of the expected channels supports notifications")
            await asyncio.sleep(seconds)
        finally:
            for uuid in subscribed:
                await client.stop_notify(available[uuid])
    return packets


def serialize_advertisement(advertisement: WatchAdvertisement) -> str:
    return json.dumps(asdict(advertisement), sort_keys=True)
