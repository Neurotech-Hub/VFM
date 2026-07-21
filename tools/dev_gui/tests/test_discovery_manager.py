"""Tests for DiscoveryManager MAC↔ID persistence behaviour."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sfm_gui.discovery_manager import DiscoveryManager
from sfm_gui.mac_id_registry import MacIdRegistry
from sfm_gui.protocol import CAN_ID_ANNOUNCE, CAN_ID_ACK, CAN_ID_REJOIN


MAC_A = bytes.fromhex("AABBCCDDEE01")
MAC_B = bytes.fromhex("AABBCCDDEE02")


class FakeCan:
    def __init__(self):
        self.assigns = []
        self.broadcasts = []

    def send_assign(self, mac, node_id):
        self.assigns.append((mac, node_id))
        return True

    def send_broadcast(self, cmd, payload=b""):
        self.broadcasts.append((cmd, payload))
        return True


class FakeIO:
    def __init__(self):
        self.aeo = []

    def drive_aeo(self, high: bool):
        self.aeo.append(high)


class TestDiscoveryMacPersistence:
    def test_announce_reuses_historical_id(self, tmp_path):
        can = FakeCan()
        io = FakeIO()
        mac_reg = MacIdRegistry(tmp_path / "r.json")
        mac_reg.set(MAC_A, 4)

        dm = DiscoveryManager(can, io, mac_reg)
        discovered = []
        dm.on_node_discovered(lambda n: discovered.append(n))
        dm.start(start_id=1)

        dm.handle_frame(CAN_ID_ANNOUNCE, MAC_A)
        assert can.assigns[-1] == (MAC_A, 4)

        dm.handle_frame(CAN_ID_ACK, MAC_A + bytes([4]))
        assert discovered[-1].node_id == 4
        assert discovered[-1].mac == MAC_A
        assert mac_reg.get_id(MAC_A) == 4

    def test_announce_new_mac_gets_next_free(self, tmp_path):
        can = FakeCan()
        io = FakeIO()
        mac_reg = MacIdRegistry(tmp_path / "r.json")
        mac_reg.set(MAC_A, 1)

        dm = DiscoveryManager(can, io, mac_reg)
        dm.start(start_id=1)

        dm.handle_frame(CAN_ID_ANNOUNCE, MAC_B)
        assert can.assigns[-1] == (MAC_B, 2)

    def test_rejoin_mismatched_id_forces_historical(self, tmp_path):
        can = FakeCan()
        io = FakeIO()
        mac_reg = MacIdRegistry(tmp_path / "r.json")
        mac_reg.set(MAC_A, 2)

        dm = DiscoveryManager(can, io, mac_reg)
        discovered = []
        dm.on_node_discovered(lambda n: discovered.append(n))
        dm.start()

        # Node NVS still has id=9, but registry says 2
        dm.handle_frame(CAN_ID_REJOIN, MAC_A + bytes([9]))
        assert can.assigns[-1] == (MAC_A, 2)
        assert discovered == []  # wait for ACK

        dm.handle_frame(CAN_ID_ACK, MAC_A + bytes([2]))
        assert discovered[-1].node_id == 2

    def test_clear_then_reassign_from_one(self, tmp_path):
        can = FakeCan()
        io = FakeIO()
        mac_reg = MacIdRegistry(tmp_path / "r.json")
        mac_reg.set(MAC_A, 7)
        mac_reg.clear()

        dm = DiscoveryManager(can, io, mac_reg)
        dm.start(clear_first=True)

        dm.handle_frame(CAN_ID_ANNOUNCE, MAC_A)
        assert can.assigns[-1] == (MAC_A, 1)
