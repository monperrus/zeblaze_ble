"""Linux read-only GATT discovery fallback using BlueZ's gatttool."""

from __future__ import annotations

import asyncio
import re


SERVICE = re.compile(r"attr handle = (0x[0-9a-f]+), end grp handle = (0x[0-9a-f]+) uuid: ([0-9a-f-]+)")
CHARACTERISTIC = re.compile(
    r"handle = (0x[0-9a-f]+), char properties = (0x[0-9a-f]+), char value handle = (0x[0-9a-f]+), uuid = ([0-9a-f-]+)"
)


async def _gatttool(address: str, option: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "gatttool", "-i", "hci0", "-b", address, option,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(stderr.decode().strip() or f"gatttool exited with {process.returncode}")
    return stdout.decode()


async def inspect(address: str) -> dict[str, object]:
    """Return primary services and characteristics without pairing or writing."""
    # The watch permits one LE central connection at a time.
    primary = await _gatttool(address, "--primary")
    characteristics = await _gatttool(address, "--characteristics")
    return {
        "address": address,
        "backend": "gatttool",
        "services": [
            {"start_handle": start, "end_handle": end, "uuid": uuid}
            for start, end, uuid in SERVICE.findall(primary)
        ],
        "characteristics": [
            {"declaration_handle": declaration, "properties": properties, "value_handle": value, "uuid": uuid}
            for declaration, properties, value, uuid in CHARACTERISTIC.findall(characteristics)
        ],
    }
