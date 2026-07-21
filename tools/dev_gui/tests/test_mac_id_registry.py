"""Tests for sfm_gui.mac_id_registry."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sfm_gui.mac_id_registry import MacIdRegistry, parse_mac


MAC_A = bytes.fromhex("AABBCCDDEE01")
MAC_B = bytes.fromhex("AABBCCDDEE02")
MAC_C = bytes.fromhex("AABBCCDDEE03")


class TestParseMac:
    def test_colon_hex(self):
        assert parse_mac("AA:BB:CC:DD:EE:01") == MAC_A

    def test_lowercase(self):
        assert parse_mac("aa:bb:cc:dd:ee:01") == MAC_A

    def test_invalid(self):
        with pytest.raises(ValueError):
            parse_mac("bad")


class TestMacIdRegistry:
    def test_set_get_persists(self, tmp_path):
        path = tmp_path / "mac_id_registry.json"
        reg = MacIdRegistry(path)
        assert len(reg) == 0

        reg.set(MAC_A, 1)
        reg.set(MAC_B, 2)
        assert reg.get_id(MAC_A) == 1
        assert reg.get_id(MAC_B) == 2
        assert reg.get_mac(1) == MAC_A
        assert path.is_file()

        # Reload from disk
        reg2 = MacIdRegistry(path)
        assert len(reg2) == 2
        assert reg2.get_id(MAC_A) == 1
        assert reg2.get_id(MAC_B) == 2

    def test_reuse_past_id(self, tmp_path):
        path = tmp_path / "mac_id_registry.json"
        reg = MacIdRegistry(path)
        reg.set(MAC_A, 4)
        assert reg.get_id(MAC_A) == 4
        assert reg.next_free_id(1) == 1  # 1 is free; 4 reserved

    def test_next_free_skips_used(self, tmp_path):
        reg = MacIdRegistry(tmp_path / "r.json")
        reg.set(MAC_A, 1)
        reg.set(MAC_B, 2)
        assert reg.next_free_id(1) == 3

    def test_set_moves_mac_away_from_old_id(self, tmp_path):
        reg = MacIdRegistry(tmp_path / "r.json")
        reg.set(MAC_A, 1)
        reg.set(MAC_A, 5)  # reassign same MAC
        assert reg.get_id(MAC_A) == 5
        assert reg.get_mac(1) is None
        assert reg.get_mac(5) == MAC_A

    def test_set_steals_id_from_other_mac(self, tmp_path):
        reg = MacIdRegistry(tmp_path / "r.json")
        reg.set(MAC_A, 3)
        reg.set(MAC_B, 3)  # ID 3 moves to B
        assert reg.get_id(MAC_B) == 3
        assert reg.get_id(MAC_A) is None

    def test_clear_wipes_file(self, tmp_path):
        path = tmp_path / "r.json"
        reg = MacIdRegistry(path)
        reg.set(MAC_A, 1)
        reg.set(MAC_C, 7)
        reg.clear()
        assert len(reg) == 0
        assert MacIdRegistry(path).get_id(MAC_A) is None

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["mappings"] == {}

    def test_corrupt_file_starts_empty(self, tmp_path):
        path = tmp_path / "r.json"
        path.write_text("not json {{{", encoding="utf-8")
        reg = MacIdRegistry(path)
        assert len(reg) == 0
