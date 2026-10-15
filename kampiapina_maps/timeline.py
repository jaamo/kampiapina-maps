"""Animation timing. All times in seconds."""

from dataclasses import dataclass


@dataclass
class Timeline:
    duration: float = 12.0          # the zoomed-in view holds from zoom_end until here
    route_fade_in: float = 0.3      # route fades in over the overview map
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
    pulse_period: float = 1.1

    @property
    def marker_travel_km(self) -> float:
        return (self.duration - self.marker_start) * self.marker_speed_kmps

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
