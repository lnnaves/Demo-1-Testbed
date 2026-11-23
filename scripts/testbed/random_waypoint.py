"""Random Waypoint mobility, generated as an ordinary trajectory (D19).

The Random Waypoint model (Johnson and Maltz, 1996; survey by Camp, Boleng
and Davies, 2002): repeatedly pick a destination uniformly inside the area,
a speed uniformly in [speed_min, speed_max], move in a straight line to the
destination, pause for a time uniform in [pause_min, pause_max].

The whole path is drawn once, from the seed, before the run, and returned as
[t, x, y, z] waypoints; the testbed then moves the node like any node with a
trajectory (mobility.py), so t=0 is the sender start and nothing moves while
the mesh forms. Differences from the Mininet-WiFi built-in model: starts at
the node's configured position (no steady-state initialization), 3D, speed
in m/s and pause in s, path recorded. speed_min must be > 0: with 0 the mean
speed decays over time (Yoon, Liu and Noble, 2003).
"""

from __future__ import annotations

import math
import random
from typing import Sequence

# A leg shorter than this (in seconds) is not worth a waypoint; destinations
# are continuous, so this only guards against degenerate draws.
_MIN_LEG_SECONDS = 1e-6


def generate(
    position: Sequence[float],
    area_min: Sequence[float],
    area_max: Sequence[float],
    speed: Sequence[float],
    pause: Sequence[float],
    seed: int,
    duration: float,
) -> list[list[float]]:
    """Waypoints [t, x, y, z] from t=0 at `position` until t=`duration`.

    Same arguments (seed included) always give the same path.
    """
    rng = random.Random(seed)
    current = [float(value) for value in position]
    t = 0.0
    waypoints = [[0.0, *current]]
    while t < duration:
        destination = [rng.uniform(low, high) for low, high in zip(area_min, area_max)]
        velocity = rng.uniform(speed[0], speed[1])
        leg = math.dist(current, destination) / velocity
        if leg < _MIN_LEG_SECONDS:
            continue
        if t + leg >= duration:
            # Cut the last leg at the end of the generated time.
            fraction = (duration - t) / leg
            current = [a + (b - a) * fraction for a, b in zip(current, destination)]
            waypoints.append([float(duration), *current])
            break
        t += leg
        current = destination
        waypoints.append([t, *current])
        wait = rng.uniform(pause[0], pause[1])
        if wait > 0:
            if t + wait >= duration:
                waypoints.append([float(duration), *current])
                break
            t += wait
            waypoints.append([t, *current])
    return waypoints


def new_seed() -> int:
    """Seed for a config that has none; it is recorded so the path can be repeated."""
    return random.SystemRandom().randrange(2**31)
