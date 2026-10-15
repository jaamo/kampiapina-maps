"""Animation timing. All times in seconds."""

from dataclasses import dataclass


@dataclass
class Timeline:
    duration: float = 12.0          # the zoomed-in view holds from zoom_end until here
    labels_start: float = 1.2       # first city starts typing
    label_stagger: float = 0.15     # delay between cities
    char_time: float = 0.03         # per letter
    dot_pop: float = 0.2            # city dot pop-in
    zoom_start: float = 3.4
    zoom_end: float = 5.6
    labels_fade: float = 0.8        # city labels fade out from zoom_start
    marker_start: float = 5.0       # marker pops in and starts moving
    marker_pop: float = 0.3
    marker_speed_kmps: float = 0.05  # how fast the marker travels along the track
    marker_ramp: float = 1.0        # marker eases up to speed over this long
    pulse_period: float = 1.1

    # Camera drift, so the map never stands still. In zoom levels per second.
    overview_zoom_rate: float = 0.05  # slow push-in on the full route
    follow_zoom_rate: float = 0.04    # slow push-in while following the marker

    # Motion blur: a frame averages sub-frames spread over `shutter` of the frame interval
    # (0.5 = film-style 180° shutter). More sub-frames are used the faster the camera moves.
    # The shutter shortens when the streak would exceed max_blur_px, keeping fast zooms readable.
    shutter: float = 0.5
    max_blur_px: float = 48.0
    blur_px_per_sample: float = 4.0

    def marker_km_offset(self, t: float) -> float:
        """Distance the marker has travelled at time t, easing up to speed."""
        tau = t - self.marker_start
        if tau <= 0:
            return 0.0
        r = self.marker_ramp
        d = tau * tau / (2 * r) if tau < r else tau - r / 2
        return d * self.marker_speed_kmps

    def label_start(self, i: int) -> float:
        return self.labels_start + i * self.label_stagger


def clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def ease_in_out_cubic(x: float) -> float:
    x = clamp01(x)
    return 4 * x**3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_out_cubic(x: float) -> float:
    return 1 - (1 - clamp01(x)) ** 3


def ease_out_back(x: float, overshoot: float = 1.9) -> float:
    x = clamp01(x)
    return 1 + (overshoot + 1) * (x - 1) ** 3 + overshoot * (x - 1) ** 2
