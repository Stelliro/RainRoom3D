# app/audio/weather.py
# Weather model for RainRoom3D (Umbra v1.4) — deterministic, slider-driven.
# No noise sources; parameter curves only.
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, List
import math

@dataclass
class WeatherParams:
    # Sliders / knobs
    # Values 0..1 unless noted
    base_intensity: float = 0.3         # average rain intensity
    variability: float = 0.5            # how much it oscillates over time
    change_speed: float = 0.5           # how fast it changes (higher = faster)
    storm_bias: float = 0.5             # chance to trend toward storms
    wind_speed_max: float = 10.0        # m/s ceiling
    wind_gustiness: float = 0.5         # gust amplitude 0..1
    wind_dir_base_deg: float = 90.0     # base direction (easterly)
    wind_dir_variation: float = 0.4     # 0..1 amount of swing
    surface: str = "glass"              # default material

def frames_from_params(p: WeatherParams, total_s: float=60.0, fps: float=20.0):
    """Yield WeatherFrame-like dicts for engine.gen_weather_events"""
    N = int(total_s * fps)
    for i in range(N):
        t = i / fps
        wobble = p.variability * 0.5 * math.sin(2*math.pi*t*(0.02 + 0.18*p.change_speed))
        trend = p.storm_bias * 0.4 * (0.5 + 0.5*math.sin(2*math.pi*t*0.01))
        rain = max(0.0, min(1.0, p.base_intensity + wobble + trend))
        dir_deg = (p.wind_dir_base_deg + 60.0*p.wind_dir_variation*math.sin(2*math.pi*t*0.015)) % 360
        gust = (0.2 + 0.8*p.wind_gustiness) * (0.4 + 0.6*(0.5+0.5*math.sin(2*math.pi*t*0.2)))
        wind_speed = gust * p.wind_speed_max
        bright = 0.8 if rain < 0.25 else 0.35
        yield {"rain_intensity": float(rain),
               "wind_dir_deg": float(dir_deg),
               "wind_speed": float(wind_speed),
               "material": p.surface,
               "brightness": float(bright)}
