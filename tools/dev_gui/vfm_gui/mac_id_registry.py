"""
mac_id_registry.py — Persistent MAC ↔ Node ID dictionary for the base station.

Stores assignments so a returning module (same MAC) gets the same CAN Node ID
it had in a previous session, instead of being auto-assigned a new sequential ID.

File format (JSON)::

    {
      "version": 1,
      "mappings": {
        "AA:BB:CC:DD:EE:01": 1,
        "AA:BB:CC:DD:EE:02": 2
      }
    }

Default path: ``~/.vfm/mac_id_registry.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from .protocol import format_mac


DEFAULT_REGISTRY_PATH = Path("~/.vfm/mac_id_registry.json")


def parse_mac(mac_str: str) -> bytes:
    """Parse 'AA:BB:CC:DD:EE:FF' (or lowercase / dashed) into 6 bytes."""
    cleaned = mac_str.strip().replace("-", ":").upper()
    parts = cleaned.split(":")
    if len(parts) != 6:
        raise ValueError(f"Invalid MAC string: {mac_str!r}")
    return bytes(int(p, 16) for p in parts)


class MacIdRegistry:
    """
    Bidirectional MAC ↔ Node ID map with JSON file persistence.

    Enforces uniqueness: each MAC maps to at most one ID, and each ID maps
    to at most one MAC.
    """

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self._path = Path(path or DEFAULT_REGISTRY_PATH).expanduser().resolve()
        self._mac_to_id: Dict[str, int] = {}
        self._id_to_mac: Dict[int, str] = {}
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        """Load mappings from disk. Missing / corrupt file → empty registry."""
        self._mac_to_id.clear()
        self._id_to_mac.clear()
        if not self._path.is_file():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            mappings = data.get("mappings", {})
            if not isinstance(mappings, dict):
                return
            for mac_str, node_id in mappings.items():
                if not isinstance(node_id, int) or not (1 <= node_id <= 254):
                    continue
                try:
                    parse_mac(str(mac_str))  # validate
                except ValueError:
                    continue
                key = str(mac_str).upper()
                # Last write wins on conflicts during load
                self._put(key, node_id)
        except (OSError, json.JSONDecodeError, TypeError):
            self._mac_to_id.clear()
            self._id_to_mac.clear()

    def save(self) -> None:
        """Write the current dictionary to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "mappings": dict(sorted(self._mac_to_id.items(), key=lambda kv: kv[1])),
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        tmp.replace(self._path)

    # ------------------------------------------------------------------
    # Dictionary API
    # ------------------------------------------------------------------

    def get_id(self, mac: bytes) -> Optional[int]:
        """Return the historically assigned Node ID for ``mac``, or None."""
        return self._mac_to_id.get(format_mac(mac))

    def get_mac(self, node_id: int) -> Optional[bytes]:
        """Return the MAC historically assigned to ``node_id``, or None."""
        mac_str = self._id_to_mac.get(node_id)
        if mac_str is None:
            return None
        return parse_mac(mac_str)

    def set(self, mac: bytes, node_id: int) -> None:
        """
        Record / update a MAC ↔ ID mapping and persist immediately.

        If ``mac`` previously had a different ID, that old mapping is removed.
        If ``node_id`` was previously owned by a different MAC, that MAC is
        removed so the dictionary stays bidirectional.
        """
        if not (1 <= node_id <= 254):
            raise ValueError(f"node_id must be 1–254, got {node_id}")
        if len(mac) != 6:
            raise ValueError("mac must be 6 bytes")
        self._put(format_mac(mac), node_id)
        self.save()

    def remove_mac(self, mac: bytes) -> None:
        """Remove a MAC (and its ID) from the dictionary and persist."""
        key = format_mac(mac)
        old_id = self._mac_to_id.pop(key, None)
        if old_id is not None:
            self._id_to_mac.pop(old_id, None)
            self.save()

    def clear(self) -> None:
        """Wipe the dictionary and delete / rewrite the file."""
        self._mac_to_id.clear()
        self._id_to_mac.clear()
        self.save()

    def next_free_id(self, start: int = 1) -> int:
        """Lowest unused Node ID >= ``start`` (clamped to 1–254)."""
        i = max(1, start)
        used = set(self._id_to_mac.keys())
        while i <= 254 and i in used:
            i += 1
        if i > 254:
            raise RuntimeError("No free node IDs remaining (1–254 exhausted)")
        return i

    def max_id(self) -> int:
        """Highest assigned ID, or 0 if the registry is empty."""
        return max(self._id_to_mac.keys()) if self._id_to_mac else 0

    def all_mappings(self) -> Dict[str, int]:
        """Return a copy of MAC-string → ID mappings."""
        return dict(self._mac_to_id)

    def __len__(self) -> int:
        return len(self._mac_to_id)

    def __contains__(self, mac: bytes) -> bool:
        return format_mac(mac) in self._mac_to_id

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _put(self, mac_str: str, node_id: int) -> None:
        # Drop previous ID for this MAC
        old_id = self._mac_to_id.get(mac_str)
        if old_id is not None and old_id != node_id:
            self._id_to_mac.pop(old_id, None)

        # Drop previous MAC for this ID
        prev_mac = self._id_to_mac.get(node_id)
        if prev_mac is not None and prev_mac != mac_str:
            self._mac_to_id.pop(prev_mac, None)

        self._mac_to_id[mac_str] = node_id
        self._id_to_mac[node_id] = mac_str
