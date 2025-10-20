Weather + Loudness (v1.4)
- Master output gain with simple lookahead limiter (targets ~-1 dBFS).
- Weather frames (rain intensity, wind direction/speed, surface) drive drop spawns.
- Wind biases impact positions; intensity ramps allow storms vs light rain.
- Tools: run_weather_demo.bat renders 60 s to out/weather_demo.wav.
- UI-friendly sliders schema in configs/weather_controls.json (hook into your front-end).
NO WHITE NOISE anywhere; still event-only modal synthesis.
