"""Chunked request/response transport over the watch's 16186f01/16186f02 pair.

Both characteristics are used bidirectionally: whichever side originates a
message writes a 6-byte header frame announcing a chunk count, the other side
acks readiness, the originator writes each data chunk, and the other side
acks completion. See protocol.py for the exact frame shapes, derived from a
live capture of the official app.

This module performs the tool's first real protocol *writes* (see plan-
full-ble-encryption.md): everything else in this package is read-only.
"""

from __future__ import annotations

import asyncio

from bleak import BleakClient

from . import protocol

# Observed ATT MTU after negotiation in the live capture; used to size chunks.
_MAX_CHUNK_BYTES = 180
_FRAME_TIMEOUT_SECONDS = 5.0


class ChunkedChannel:
    """Owns the notify queue and ack/data handshake for one characteristic."""

    def __init__(self, client: BleakClient, characteristic_uuid: str) -> None:
        self._client = client
        self._uuid = characteristic_uuid
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    def _on_notify(self, _sender: object, data: bytearray) -> None:
        self._queue.put_nowait(bytes(data))

    async def start(self) -> None:
        await self._client.start_notify(self._uuid, self._on_notify)

    async def stop(self) -> None:
        await self._client.stop_notify(self._uuid)

    async def _next_frame(self) -> bytes:
        return await asyncio.wait_for(self._queue.get(), timeout=_FRAME_TIMEOUT_SECONDS)

    async def _write(self, frame: bytes) -> None:
        await self._client.write_gatt_char(self._uuid, frame, response=False)

    async def send_message(self, payload: bytes) -> None:
        """Originate a message: header, wait ready-ack, chunks, wait complete-ack."""
        chunks = [payload[i : i + _MAX_CHUNK_BYTES] for i in range(0, len(payload), _MAX_CHUNK_BYTES)] or [b""]
        await self._write(protocol.header_frame(len(chunks)))
        ack = await self._next_frame()
        if not protocol.is_ready_ack(ack):
            raise RuntimeError(f"expected ready-ack, got {ack.hex()}")
        for index, chunk in enumerate(chunks, start=1):
            await self._write(protocol.data_chunk(index, chunk))
        ack = await self._next_frame()
        if not protocol.is_complete_ack(ack):
            raise RuntimeError(f"expected complete-ack, got {ack.hex()}")

    async def receive_message(self) -> bytes:
        """Wait for the other side to originate a message; ack it and reassemble the payload."""
        header = await self._next_frame()
        if not protocol.is_header_frame(header):
            raise RuntimeError(f"expected header frame, got {header.hex()}")
        count = protocol.chunk_count_from_header(header)
        await self._write(protocol.ACK_READY)
        chunks: dict[int, bytes] = {}
        for _ in range(count):
            frame = await self._next_frame()
            index, chunk = protocol.split_data_chunk(frame)
            chunks[index] = chunk
        await self._write(protocol.ACK_COMPLETE)
        return b"".join(chunks[index] for index in sorted(chunks))


async def request_device_info(address: str) -> protocol.DeviceInfo:
    """Connect, send GET_DEVICE_INFO (id 32), and return the parsed response.

    No pairing/bonding is performed or required: the live capture this is
    based on ran over an unencrypted, unbonded link.
    """
    # This machine's bluetoothd auto-probes the watch's HID-over-GATT service on
    # every connect and those reads fail (encryption required, which this link
    # doesn't use), which can delay BlueZ's ServicesResolved signal well past
    # bleak's default timeout even though the connection itself is fine.
    async with BleakClient(address, timeout=90) as client:
        write_channel = ChunkedChannel(client, protocol.COMMAND_WRITE)
        read_channel = ChunkedChannel(client, protocol.COMMAND_READ)
        await write_channel.start()
        await read_channel.start()
        try:
            await write_channel.send_message(protocol.encode_request(protocol.CMD_GET_DEVICE_INFO))
            payload = await read_channel.receive_message()
        finally:
            await write_channel.stop()
            await read_channel.stop()
    return protocol.parse_device_info(payload)
