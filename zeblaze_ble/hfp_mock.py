"""Minimal local HFP Audio Gateway for the Zeblaze watch.

This deliberately implements only the harmless RFCOMM AT commands the watch
uses while linking a companion phone.  It reports an in-service, idle phone,
but does not expose or control a real modem, calls, contacts, or messages.
"""

from __future__ import annotations

import datetime as dt
import logging
import socket
import threading

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

BLUEZ = "org.bluez"
PROFILE_MANAGER = "/org/bluez"
PROFILE_PATH = "/org/zeblaze/HfpAudioGateway"
HFP_AG_UUID = "0000111f-0000-1000-8000-00805f9b34fb"

LOG = logging.getLogger(__name__)


def _clock() -> str:
    """Return an HFP CCLK timestamp in local time, including timezone quarters."""
    now = dt.datetime.now().astimezone()
    offset = now.utcoffset() or dt.timedelta()
    quarters = int(offset.total_seconds() // 900)
    return f'\r\n+CCLK: "{now:%y/%m/%d,%H:%M:%S}{quarters:+03d}"\r\n'


def _ok() -> str:
    """HFP result-code framing (the leading CRLF is required)."""
    return "\r\nOK\r\n"


def _reply(command: str) -> str:
    """Return the static, idle-phone answer for one complete AT command."""
    command = command.strip()
    upper = command.upper()
    LOG.info("HFP <- %s", command)
    if upper.startswith("AT+BRSF="):
        # Advertise the conservative baseline: this watch is an older HFP
        # client and must not be led into optional HF-indicator/codec flows
        # that this intentionally call-less emulator does not implement.
        return "\r\n+BRSF: 0\r\n" + _ok()
    if upper.startswith("AT+CIND=?"):
        return (
            '\r\n+CIND: ("service",(0,1)),("call",(0,1)),'
            '("callsetup",(0-3)),("callheld",(0-2)),("signal",(0-5)),'
            '("roam",(0,1)),("battchg",(0-5))\r\n' + _ok()
        )
    if upper == "AT+CIND?":
        return "\r\n+CIND: 1,0,0,0,5,0,5\r\n" + _ok()
    if upper == "AT+BTRH?":
        return "\r\n+BTRH: 0\r\n" + _ok()
    if upper == "AT+CGMI":
        return "\r\n+CGMI: Codex Local HFP\r\n" + _ok()
    if upper == "AT+CGMM":
        return "\r\n+CGMM: Zeblaze companion\r\n" + _ok()
    if upper == "AT+CCLK?":
        return _clock() + _ok()
    if upper == "AT+COPS?":
        return '\r\n+COPS: 0,0,"Local",7\r\n' + _ok()
    # Codec negotiation, indicator reporting, caller-ID configuration,
    # character-set selection, echo/noise-reduction settings, and status
    # queries all get a successful but non-telephony response.
    if upper.startswith(
        (
            "AT+BAC=",
            "AT+CMER=",
            "AT+CHLD=",
            "AT+CLIP=",
            "AT+CCWA=",
            "AT+NREC=",
            "AT+CSCS=",
            "AT+COPS=",
            "AT+CMEE=",
            "AT+VGS=",
            "AT+VGM=",
        )
    ):
        return _ok()
    return "\r\nERROR\r\n"


def _serve(fd: int) -> None:
    """Read RFCOMM commands and answer them until the peer disconnects."""
    sock = socket.socket(fileno=fd)
    # BlueZ passes a non-blocking Unix FD.  This dedicated worker owns it, so
    # make reads blocking rather than mistaking EAGAIN for a remote disconnect.
    sock.setblocking(True)
    pending = b""
    try:
        while data := sock.recv(1024):
            pending += data
            while b"\r" in pending:
                raw, pending = pending.split(b"\r", 1)
                if not raw:
                    continue
                response = _reply(raw.decode("ascii", errors="replace"))
                LOG.info("HFP -> %s", response.replace("\r", "\\r").replace("\n", "\\n"))
                sock.sendall(response.encode("ascii"))
    except OSError as error:
        LOG.info("HFP RFCOMM closed: %s", error)
    finally:
        sock.close()


class HfpAudioGateway(dbus.service.Object):
    """BlueZ Profile1 implementation that supplies an HFP Audio Gateway."""

    @dbus.service.method(BLUEZ + ".Profile1", in_signature="", out_signature="")
    def Release(self) -> None:  # noqa: N802 - D-Bus API spelling
        LOG.info("BlueZ released the mock HFP profile")

    @dbus.service.method(BLUEZ + ".Profile1", in_signature="oha{sv}", out_signature="")
    def NewConnection(self, device: dbus.ObjectPath, fd: dbus.types.UnixFd, properties: dbus.Dictionary) -> None:  # noqa: N802
        del properties
        owned_fd = fd.take()
        LOG.info("HFP connected from %s", device)
        threading.Thread(target=_serve, args=(owned_fd,), daemon=True).start()

    @dbus.service.method(BLUEZ + ".Profile1", in_signature="o", out_signature="")
    def RequestDisconnection(self, device: dbus.ObjectPath) -> None:  # noqa: N802
        LOG.info("HFP disconnect requested by %s", device)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    HfpAudioGateway(bus, PROFILE_PATH)
    manager = dbus.Interface(bus.get_object(BLUEZ, PROFILE_MANAGER), BLUEZ + ".ProfileManager1")
    manager.RegisterProfile(
        PROFILE_PATH,
        HFP_AG_UUID,
        {
            "Name": "Zeblaze local phone",
            "Role": "server",
            "RequireAuthentication": False,
            "RequireAuthorization": False,
        },
    )
    LOG.info("registered mock HFP Audio Gateway")
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
