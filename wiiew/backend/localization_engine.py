"""
Wiiew Multi-Node Room Localization Engine (Phase 3).

Provides spatial multi-node CSI aggregation, room geometric bounds,
and honest position estimation.

Honesty & Zero Fabrication Policy:
- Single node CSI link can only detect presence and motion level.
- Multi-node spatial localization requires >= 3 active calibrated nodes.
- When < 3 nodes are active, valid=False and coordinates (x, y) are strictly null.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from .config import WiiewSettings


class NodeState(BaseModel):
    node_id: str
    name: str = ""
    ip: str = ""
    x: float = 0.0
    y: float = 0.0
    z: float = 1.2
    enabled: bool = True
    last_seen: float = 0.0
    presence: bool = False
    rssi_dbm: float = -80.0
    mean_amplitude: float = 0.0
    variance: float = 0.0
    motion_band_power: float = 0.0


class LocalizationResult(BaseModel):
    valid: bool = False
    state: str = "NO_LOCALIZATION"  # NO_LOCALIZATION, SINGLE_NODE, LOW_CONFIDENCE, VALID_ESTIMATE
    x: Optional[float] = None
    y: Optional[float] = None
    confidence: float = 0.0
    active_nodes: int = 0
    total_nodes: int = 0
    room_width_m: float = 4.0
    room_depth_m: float = 5.0
    method: str = "none"
    message: str = "No active CSI nodes available."
    nodes: List[Dict[str, Any]] = Field(default_factory=list)


class LocalizationEngine:
    """
    Tracks per-node measurements and computes room coordinates
    using multi-static RF field variance weighting when >= 3 nodes exist.
    """

    def __init__(self, settings: WiiewSettings) -> None:
        self.settings = settings
        self.nodes: Dict[str, NodeState] = {}
        self._init_nodes()

    def _init_nodes(self) -> None:
        """Initialize known nodes from settings."""
        self.nodes.clear()
        for cfg in self.settings.nodes:
            nid = str(cfg.get("node_id", f"node_{len(self.nodes) + 1}"))
            self.nodes[nid] = NodeState(
                node_id=nid,
                name=cfg.get("name", nid),
                ip=cfg.get("ip", ""),
                x=float(cfg.get("x", 0.0)),
                y=float(cfg.get("y", 0.0)),
                z=float(cfg.get("z", 1.2)),
                enabled=bool(cfg.get("enabled", True)),
            )

    def update_node_sample(
        self,
        node_id: str,
        presence: bool,
        rssi: float,
        amplitude: float,
        variance: float = 0.0,
        motion_band_power: float = 0.0,
        timestamp: Optional[float] = None,
    ) -> None:
        """Record measurement from a specific CSI node."""
        now = timestamp or time.time()
        if node_id not in self.nodes:
            # Dynamically register newly seen node if not configured
            self.nodes[node_id] = NodeState(
                node_id=node_id,
                name=f"Node {node_id}",
                enabled=True,
            )

        node = self.nodes[node_id]
        node.last_seen = now
        node.presence = presence
        node.rssi_dbm = rssi
        node.mean_amplitude = amplitude
        node.variance = variance
        node.motion_band_power = motion_band_power

    def compute_localization(self, now: Optional[float] = None) -> LocalizationResult:
        """
        Evaluate node health and compute truthful 2D coordinates.
        Requires >= 3 active nodes to produce valid coordinates.
        """
        current_time = now or time.time()
        active_nodes: List[NodeState] = []
        node_snapshots: List[Dict[str, Any]] = []

        # Check node health and staleness (5.0 seconds timeout)
        for nid, node in self.nodes.items():
            is_active = (
                node.enabled
                and (current_time - node.last_seen <= 5.0)
                and (node.last_seen > 0.0)
            )
            ago = round(current_time - node.last_seen, 1) if node.last_seen > 0 else None

            if is_active:
                active_nodes.append(node)

            node_snapshots.append({
                "node_id": node.node_id,
                "name": node.name,
                "ip": node.ip,
                "x": node.x,
                "y": node.y,
                "enabled": node.enabled,
                "active": is_active,
                "last_seen_seconds_ago": ago,
                "variance": round(node.variance, 2),
                "motion_band_power": round(node.motion_band_power, 2),
            })

        active_count = len(active_nodes)
        total_count = len(self.nodes)
        width = float(self.settings.room_width_m)
        depth = float(self.settings.room_depth_m)

        # 0 Active Nodes
        if active_count == 0:
            return LocalizationResult(
                valid=False,
                state="NO_LOCALIZATION",
                x=None,
                y=None,
                confidence=0.0,
                active_nodes=0,
                total_nodes=total_count,
                room_width_m=width,
                room_depth_m=depth,
                method="none",
                message="No active CSI nodes available.",
                nodes=node_snapshots,
            )

        # 1 Active Node: Single node baseline (Zero fabrication)
        if active_count == 1:
            return LocalizationResult(
                valid=False,
                state="SINGLE_NODE",
                x=None,
                y=None,
                confidence=0.0,
                active_nodes=1,
                total_nodes=total_count,
                room_width_m=width,
                room_depth_m=depth,
                method="single_node_baseline",
                message="Localization requires multiple calibrated CSI nodes.",
                nodes=node_snapshots,
            )

        # 2 Active Nodes: Bistatic baseline (Insufficient for unambiguous 2D plane intersection)
        if active_count == 2:
            return LocalizationResult(
                valid=False,
                state="LOW_CONFIDENCE",
                x=None,
                y=None,
                confidence=0.20,
                active_nodes=2,
                total_nodes=total_count,
                room_width_m=width,
                room_depth_m=depth,
                method="bistatic_pair",
                message="2 nodes detected: insufficient geometric diversity for 2D position.",
                nodes=node_snapshots,
            )

        # >= 3 Active Nodes: Multi-static centroid / multilateration
        total_weight = 0.0
        weighted_x = 0.0
        weighted_y = 0.0

        for n in active_nodes:
            # Disturbance weight: higher variance & motion band power indicates human proximity
            # Base floor of 0.05 ensures geometric contribution even under quiet breathing
            w = max(0.05, n.variance + 4.0 * n.motion_band_power)
            total_weight += w
            weighted_x += w * n.x
            weighted_y += w * n.y

        if total_weight > 0:
            est_x = max(0.0, min(width, weighted_x / total_weight))
            est_y = max(0.0, min(depth, weighted_y / total_weight))
            confidence = min(0.95, round(0.40 + 0.15 * active_count, 2))

            return LocalizationResult(
                valid=True,
                state="VALID_ESTIMATE",
                x=round(est_x, 2),
                y=round(est_y, 2),
                confidence=confidence,
                active_nodes=active_count,
                total_nodes=total_count,
                room_width_m=width,
                room_depth_m=depth,
                method="variance_weighted_multilateration",
                message=f"2D position estimated from {active_count} calibrated CSI nodes.",
                nodes=node_snapshots,
            )

        return LocalizationResult(
            valid=False,
            state="LOW_CONFIDENCE",
            x=None,
            y=None,
            confidence=0.1,
            active_nodes=active_count,
            total_nodes=total_count,
            room_width_m=width,
            room_depth_m=depth,
            method="insufficient_weights",
            message="Active nodes reported zero signal disturbance.",
            nodes=node_snapshots,
        )
