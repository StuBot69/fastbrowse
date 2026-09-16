#!/usr/bin/env python3
"""
Human-like mouse movement for Playwright / Camoufox.
Drop-in replacement for page.mouse.move(x, y) that follows a Bezier curve
with variable speed, micro-jitter, and realistic acceleration/deceleration.
"""

import math
import random
import time
from typing import Literal, Optional, Tuple

from playwright.sync_api import Page


def _bezier_point(t: float, p0: Tuple[float, float], p1: Tuple[float, float],
                  p2: Tuple[float, float], p3: Tuple[float, float]) -> Tuple[float, float]:
    """Cubic Bezier point at parameter t in [0, 1]."""
    u = 1 - t
    uu = u * u
    uuu = uu * u
    tt = t * t
    ttt = tt * t
    x = (uuu * p0[0]) + (3 * uu * t * p1[0]) + (3 * u * tt * p2[0]) + (ttt * p3[0])
    y = (uuu * p0[1]) + (3 * uu * t * p1[1]) + (3 * u * tt * p2[1]) + (ttt * p3[1])
    return (x, y)


def _generate_control_points(start: Tuple[float, float],
                             end: Tuple[float, float]) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Generate two control points for a natural-looking curve."""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dist = math.hypot(dx, dy)

    if dist < 1:
        return start, end

    perp_x = -dy / dist
    perp_y = dx / dist
    bulge = dist * (0.05 + random.random() * 0.20)

    mid1_x = start[0] + dx / 3 + perp_x * bulge * (1 if random.random() > 0.5 else -1)
    mid1_y = start[1] + dy / 3 + perp_y * bulge * (1 if random.random() > 0.5 else -1)
    mid2_x = start[0] + 2 * dx / 3 + perp_x * bulge * (1 if random.random() > 0.5 else -1)
    mid2_y = start[1] + 2 * dy / 3 + perp_y * bulge * (1 if random.random() > 0.5 else -1)

    return (mid1_x, mid1_y), (mid2_x, mid2_y)


def _ease_in_out_cubic(t: float) -> float:
    """Cubic ease-in-out: slow start, fast middle, slow end."""
    if t < 0.5:
        return 4 * t * t * t
    return 1 - pow(-2 * t + 2, 3) / 2


def _get_mouse_pos(page: Page) -> Tuple[float, float]:
    """Get stored mouse position, defaulting to (0, 0)."""
    if not hasattr(page, "_human_mouse_pos"):
        page._human_mouse_pos = (0.0, 0.0)  # type: ignore[attr-defined]
    return page._human_mouse_pos  # type: ignore[attr-defined]


def _set_mouse_pos(page: Page, x: float, y: float) -> None:
    page._human_mouse_pos = (float(x), float(y))  # type: ignore[attr-defined]


def human_move(page: Page,
               target_x: float, target_y: float,
               *,
               steps: Optional[int] = None,
               base_duration: float = 0.8,
               jitter_px: float = 1.5,
               overshoot_chance: float = 0.05,
               overshoot_px: float = 8.0) -> None:
    """
    Move mouse to (target_x, target_y) along a natural Bezier curve.

    Args:
        page: Playwright Page (or Camoufox page, same API).
        target_x, target_y: Destination coordinates (viewport-relative).
        steps: Number of intermediate points. Auto-calculated from distance if None.
        base_duration: Base movement time in seconds (scales with distance).
        jitter_px: Per-step positional noise (simulates hand tremor).
        overshoot_chance: Probability of slightly overshooting then correcting.
        overshoot_px: How far to overshoot if triggered.
    """
    start_x, start_y = _get_mouse_pos(page)

    dx = target_x - start_x
    dy = target_y - start_y
    distance = math.hypot(dx, dy)

    if distance < 2:
        page.mouse.move(target_x + random.uniform(-0.5, 0.5),
                        target_y + random.uniform(-0.5, 0.5))
        _set_mouse_pos(page, target_x, target_y)
        return

    if steps is None:
        steps = max(10, min(60, int(distance / 5)))

    ctrl1, ctrl2 = _generate_control_points((start_x, start_y), (target_x, target_y))
    p0 = (start_x, start_y)
    p3 = (target_x, target_y)

    duration = min(base_duration * (distance / 300), 2.5)
    step_time = duration / steps

    for i in range(1, steps + 1):
        t = i / steps
        eased = _ease_in_out_cubic(t)
        x, y = _bezier_point(eased, p0, ctrl1, ctrl2, p3)

        x += random.uniform(-jitter_px, jitter_px)
        y += random.uniform(-jitter_px, jitter_px)

        page.mouse.move(x, y)
        time.sleep(random.uniform(0.8, 1.2) * step_time)

    page.mouse.move(target_x, target_y)

    if random.random() < overshoot_chance:
        ox = target_x + random.uniform(-overshoot_px, overshoot_px)
        oy = target_y + random.uniform(-overshoot_px, overshoot_px)
        page.mouse.move(ox, oy)
        time.sleep(random.uniform(0.08, 0.15))
        page.mouse.move(target_x, target_y)

    _set_mouse_pos(page, target_x, target_y)


def human_click(page: Page,
                x: Optional[float] = None, y: Optional[float] = None,
                *,
                button: Literal["left", "middle", "right"] = "left",
                click_count: int = 1,
                delay_range: Tuple[float, float] = (0.05, 0.15)) -> None:
    """
    Move human-like to (x, y) then click. If x/y is None, clicks at current position.
    """
    if x is not None and y is not None:
        human_move(page, x, y)
    time.sleep(random.uniform(*delay_range))
    page.mouse.click(x or 0, y or 0, button=button, click_count=click_count)
    time.sleep(random.uniform(*delay_range))


def reset_mouse_tracker(page: Page, x: float = 0, y: float = 0) -> None:
    """Reset the internal position tracker (call once after page load)."""
    _set_mouse_pos(page, x, y)
    page.mouse.move(x, y)


if __name__ == "__main__":
    from camoufox.sync_api import Camoufox

    with Camoufox(headless=False) as browser:
        page = browser.new_page()
        page.goto("https://bot.sannysoft.com/")
        reset_mouse_tracker(page, 100, 100)
        human_move(page, 500, 300)
        human_click(page, 600, 400)
        time.sleep(2)
        print("Done — check the mouse trail on bot.sannysoft.com")