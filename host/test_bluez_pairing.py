import asyncio
from types import SimpleNamespace

import pytest
from dbus_fast import DBusError, MessageType, Variant

from bluez_pairing import AGENT_PATH, BoardAgent, connected_board, pair_board

PATH = "/org/bluez/hci0/dev_00_11_22_33_44_55"


class Bus:
    def __init__(self, paired=False, fail=False):
        self.paired, self.fail = paired, fail
        self.calls = []
        self.agent = None
        self.closed = False

    async def connect(self):
        return self

    async def call(self, message):
        self.calls.append(message.member)
        if message.member == "Get":
            body = [Variant("b", self.paired)]
        else:
            body = []
        if message.member == "Pair" and self.fail:
            raise OSError("pair failed")
        return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=body)

    def export(self, path, agent):
        assert path == AGENT_PATH
        self.agent = agent

    def unexport(self, path):
        assert path == AGENT_PATH

    def disconnect(self):
        self.closed = True


def test_pairing_agent_is_application_local_and_removed():
    bus = Bus()
    asyncio.run(pair_board(SimpleNamespace(details={"path": PATH}), bus_factory=lambda **kw: bus))
    assert bus.calls == ["Get", "RegisterAgent", "Pair", "UnregisterAgent"]
    assert bus.closed
    bus.agent.check(PATH)
    with pytest.raises(DBusError, match="registered board"):
        bus.agent.check(PATH + "other")


def test_existing_bond_does_not_register_an_agent():
    bus = Bus(paired=True)
    asyncio.run(pair_board(SimpleNamespace(details={"path": PATH}), bus_factory=lambda **kw: bus))
    assert bus.calls == ["Get"] and bus.closed


def test_failed_pairing_cancels_and_cleans_up():
    bus = Bus(fail=True)
    with pytest.raises(OSError, match="pair failed"):
        asyncio.run(pair_board(SimpleNamespace(details={"path": PATH}), bus_factory=lambda **kw: bus))
    assert bus.calls[-2:] == ["CancelPairing", "UnregisterAgent"]
    assert bus.closed


@pytest.mark.parametrize("paired,service,found", [(True, True, True), (False, True, False), (True, False, False)])
def test_only_registered_paired_nus_connection_can_be_reused(paired, service, found):
    address = "00:11:22:33:44:55"

    class CachedBus(Bus):
        async def call(self, message):
            assert message.member == "GetManagedObjects"
            props = dict(Address=Variant("s", address), Name=Variant("s", "Codex-P4"),
                         Paired=Variant("b", paired), Connected=Variant("b", True),
                         UUIDs=Variant("as", ["6e400001-b5a3-f393-e0a9-e50e24dcca9e"] if service else []))
            return SimpleNamespace(message_type=MessageType.METHOD_RETURN,
                                   body=[{PATH: {"org.bluez.Device1": props}}])

    bus = CachedBus()
    device = asyncio.run(connected_board(address, bus_factory=lambda **kw: bus))
    assert bool(device) == found and bus.closed
    if device:
        assert device.address == address and device.details["path"] == PATH
    assert asyncio.run(connected_board("00:11:22:33:44:66", bus_factory=lambda **kw: CachedBus())) is None
