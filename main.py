"""SN17 competition entry point: the runtime supplies one prompt image, the seed and the clients."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sn17miner.pipeline import run

if TYPE_CHECKING:
    from renderer_client import Renderer
    from witness_client import WitnessClient


def generate(image: bytes, seed: int, witness: WitnessClient, renderer: Renderer) -> str | None:
    """Return the final Three.js module for this image, or None if generation failed."""
    return run(image, seed, witness.responses, renderer)
