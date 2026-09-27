"""An application-local pairing agent for the USB-identified board only."""

import asyncio

from dbus_fast import BusType, DBusError, Message, MessageType
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method

AGENT_PATH = "/org/codexpet/PairingAgent"


async def connected_board(address, *, bus_factory=MessageBus):
    """Reuse only this bonded board if BlueZ retained it after a process crash."""
    from bleak.backends.device import BLEDevice
    bus = await bus_factory(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await asyncio.wait_for(bus.call(Message(
            destination="org.bluez", path="/", interface="org.freedesktop.DBus.ObjectManager",
            member="GetManagedObjects")), 5)
        if reply.message_type == MessageType.ERROR:
            raise OSError(f"{reply.error_name}: {reply.body}")
        for path, interfaces in reply.body[0].items():
            props = {name: value.value for name, value in interfaces.get("org.bluez.Device1", {}).items()}
            if (str(props.get("Address", "")).lower() == address.lower() and
                    props.get("Connected") and props.get("Paired") and
                    "6e400001-b5a3-f393-e0a9-e50e24dcca9e" in
                    [str(uuid).lower() for uuid in props.get("UUIDs", [])]):
                return BLEDevice(props["Address"], props.get("Name"), {"path": path, "props": props})
        return None
    finally:
        bus.disconnect()


class BoardAgent(ServiceInterface):
    def __init__(self, device_path):
        super().__init__("org.bluez.Agent1")
        self.device_path = device_path

    def check(self, device):
        if device != self.device_path:
            raise DBusError("org.bluez.Error.Rejected", "Not the registered board")

    @method()
    def Release(self):
        pass

    @method()
    def Cancel(self):
        pass

    @method()
    def RequestAuthorization(self, device: 'o'):
        self.check(device)

    @method()
    def RequestConfirmation(self, device: 'o', passkey: 'u'):
        self.check(device)

    @method()
    def AuthorizeService(self, device: 'o', uuid: 's'):
        self.check(device)
        if uuid.lower() != "6e400001-b5a3-f393-e0a9-e50e24dcca9e":
            raise DBusError("org.bluez.Error.Rejected", "Not the board UART service")

    @method()
    def RequestPinCode(self, device: 'o') -> 's':
        raise DBusError("org.bluez.Error.Rejected", "PIN pairing is not supported")

    @method()
    def RequestPasskey(self, device: 'o') -> 'u':
        raise DBusError("org.bluez.Error.Rejected", "Passkey entry is not supported")


async def pair_board(device, *, timeout=20, bus_factory=MessageBus):
    path = device.details["path"]
    bus = await bus_factory(bus_type=BusType.SYSTEM).connect()
    registered = False
    pairing = False

    async def call(path, interface, member, signature="", body=None):
        reply = await bus.call(Message(destination="org.bluez", path=path,
                                       interface=interface, member=member,
                                       signature=signature, body=body or []))
        if reply.message_type == MessageType.ERROR:
            raise OSError(f"{reply.error_name}: {reply.body}")
        return reply.body

    try:
        paired = await call(path, "org.freedesktop.DBus.Properties", "Get", "ss",
                            ["org.bluez.Device1", "Paired"])
        if paired[0].value:
            return
        bus.export(AGENT_PATH, BoardAgent(path))
        await call("/org/bluez", "org.bluez.AgentManager1", "RegisterAgent", "os",
                   [AGENT_PATH, "NoInputNoOutput"])
        registered = True
        # Pair on this same D-Bus connection so BlueZ selects our agent, without
        # replacing the desktop's default agent or approving unrelated devices.
        pairing = True
        await asyncio.wait_for(call(path, "org.bluez.Device1", "Pair"), timeout)
        pairing = False
    finally:
        if pairing:
            try:
                await asyncio.wait_for(call(path, "org.bluez.Device1", "CancelPairing"), 2)
            except Exception:
                pass
        if registered:
            try:
                await asyncio.wait_for(call("/org/bluez", "org.bluez.AgentManager1",
                                            "UnregisterAgent", "o", [AGENT_PATH]), 2)
            except Exception:
                pass
        bus.unexport(AGENT_PATH)
        bus.disconnect()
