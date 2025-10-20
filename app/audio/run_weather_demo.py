# app/audio/run_weather_demo.py
# Small launcher to avoid argparse attr mismatch issues. Calls engine.render_weather_demo() directly.
from pathlib import Path
from app.audio.engine import render_weather_demo, _load_room_example, configure_room
def main():
    # Ensure engine configured with default room and loud master
    room = _load_room_example()
    configure_room(room, sr=48000, master_gain_db=18.0)
    outp = render_weather_demo(out_path=None, total_s=60.0, sr=48000)
    print("[RainRoom3D] weather demo rendered ->", outp)
if __name__ == "__main__":
    main()
