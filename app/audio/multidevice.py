
import threading, time, math
import numpy as np
try:
    import sounddevice as sd
except Exception:
    sd = None

from app.audio.engine import RainEngine  # uses _emit_block() for source field
from app.models.room import Room

def _list_output_devices():
    """Return a list of output-capable devices as dicts {index, name, hostapi}."""
    if sd is None:
        return []
    try:
        devs = sd.query_devices()
        outs = []
        for i, d in enumerate(devs):
            if d.get('max_output_channels', 0) >= 2:
                outs.append({'index': i, 'name': d.get('name','?'), 'hostapi': d.get('hostapi', None)})
        return outs
    except Exception:
        return []

class _Bus:
    def __init__(self, device_index: int, samplerate: int, blocksize: int, get_block_fn):
        self.device_index = device_index
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.get_block_fn = get_block_fn
        self.stream = None

    def start(self):
        if sd is None:
            raise RuntimeError("sounddevice not available for multi-device output.")
        self.stream = sd.OutputStream(
            device=self.device_index,
            channels=2,
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            callback=self._callback
        )
        self.stream.start()

    def _callback(self, outdata, frames, time_info, status):
        import logging
        try:
            if status:
                logging.getLogger('audio').warning('Bus status (dev %s): %s', self.device_index, status)
            buf = self.get_block_fn(frames)  # shape (frames,2)
            outdata[:] = buf
        except Exception as e:
            logging.getLogger('audio').exception('Bus callback error: %s', e)
            outdata[:] = np.zeros_like(outdata)

    def stop(self):
        try:
            if self.stream is not None:
                self.stream.stop(); self.stream.close()
        finally:
            self.stream = None

class MultiDeviceEngine:
    """Mix the rain field to multiple physical output devices, one per spawned Speaker.
    Each Speaker can be assigned a device index (sounddevice). We pan grains across speakers by angular proximity.
    """
    def __init__(self, room: Room, samplerate=48000, blocksize=512):
        self.room = room
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.base = RainEngine(room, samplerate=samplerate, blocksize=blocksize)
        self._buses = {}  # device_index -> _Bus
        self.running = False
        self._lock = threading.Lock()
        self._devices = _list_output_devices()

    @property
    def devices(self):
        """Return cached list of output devices."""
        return list(self._devices)

    def set_speaker_device(self, speaker, device_index: int | None):
        """Attach a device index to a Speaker (stored as attribute)."""
        setattr(speaker, "audio_device", device_index)

    def _active_device_map(self):
        """Gather speakers by device index: {device_index: [Speaker, ...]}"""
        dev_map = {}
        for spk in getattr(self.room,'speakers', []):
            dev = getattr(spk, "audio_device", None)
            if dev is None:
                continue
            dev_map.setdefault(dev, []).append(spk)
        return dev_map

    def _ensure_buses(self):
        dev_map = self._active_device_map()
        # create missing
        for dev_idx in dev_map.keys():
            if dev_idx not in self._buses:
                self._buses[dev_idx] = _Bus(
                    dev_idx, self.samplerate, self.blocksize,
                    get_block_fn=lambda n, di=dev_idx: self._render_for_device(n, di)
                )
        # remove stale
        for di in list(self._buses.keys()):
            if di not in dev_map:
                self._buses[di].stop(); del self._buses[di]

    def _render_for_device(self, n, dev_idx):
        """Render a block for all speakers routed to device dev_idx. Stereo out."""
        # 1) Get a base set of grains as a stereo buffer to use as "source events".
        buf = self.base._emit_block(n)  # (n,2) limited
        mono = buf.mean(axis=1).astype(np.float32)
        out = np.zeros((n,2), np.float32)

        # 2) Speaker geometry for this device
        L = getattr(self.room, 'listener', type('L',(),{'x':self.room.width/2,'z':self.room.depth/2}))
        speakers = [s for s in getattr(self.room,'speakers',[]) if getattr(s,'audio_device',None)==dev_idx]
        if not speakers:
            return out

        # 3) Virtual moving source for gentle steering
        wind = float(getattr(self.room,'wind',0.3))
        t = time.time()
        virt_ang = (t*0.25*wind) % (2*math.pi)

        # Per speaker gain
        gains = []
        for s in speakers:
            dx=s.x - L.x; dz=s.z - L.z
            ang=math.atan2(dx,dz)
            d_ang = abs((ang - virt_ang + math.pi) % (2*math.pi) - math.pi)
            g = max(0.0, math.cos(d_ang)) ** 2.0  # cosine taper
            dist = max(0.5, math.hypot(dx,dz))
            g *= 1.0 / (1.0 + 0.3*(dist-1.0))
            gains.append(g)
        ssum = sum(gains) or 1.0
        gains = [g/ssum for g in gains]

        # 4) Distribute mono to each device equally L/R, scaled by its gain
        for g in gains:
            out[:,0] += mono * g
            out[:,1] += mono * g

        # limiter
        m = float(np.max(np.abs(out))) or 1.0
        if m > 0.98:
            out *= (0.98/m)
        return out

    def start(self):
        self._ensure_buses()
        for b in self._buses.values():
            b.start()
        self.running = True

    def stop(self):
        for b in list(self._buses.values()):
            b.stop()
        self._buses.clear()
        self.running = False
