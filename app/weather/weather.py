
import random, time, math, dataclasses

@dataclasses.dataclass
class WeatherState:
    name: str
    cloudiness: float
    storm: float
    rain: float
    wind: float
    thunder_rate: float  # strikes per minute in storms

class WeatherSystem:
    def __init__(self, rng=None):
        self.rng = random.Random() if rng is None else rng
        self.state = WeatherState('Calm', 0.2, 0.0, 0.05, 0.1, 0.0)
        self.next_change = time.time() + self.rng.uniform(60, 180)
        self._gust_until = 0.0
        self._lightning_until = 0.0
        self._last_strike = 0.0

    def _choose_next(self):
        s = self.state.name
        roll = self.rng.random()
        if s == 'Calm':
            if roll < 0.5: return WeatherState('Drizzle', 0.45, 0.0, 0.25, 0.25, 0.0)
            else: return WeatherState('Calm', 0.25, 0.0, 0.05, 0.12, 0.0)
        elif s == 'Drizzle':
            if roll < 0.4: return WeatherState('Rain', 0.6, 0.2, 0.55, 0.35, 0.5)
            elif roll < 0.6: return WeatherState('Calm', 0.25, 0.0, 0.05, 0.12, 0.0)
            else: return WeatherState('Drizzle', 0.5, 0.0, 0.3, 0.25, 0.0)
        elif s == 'Rain':
            if roll < 0.45: return WeatherState('Storm', 0.85, 0.9, 0.95, 0.6, 4.0)
            elif roll < 0.7: return WeatherState('Drizzle', 0.5, 0.0, 0.3, 0.25, 0.0)
            else: return WeatherState('Rain', 0.65, 0.25, 0.6, 0.35, 0.5)
        else: # Storm
            if roll < 0.5: return WeatherState('Rain', 0.65, 0.25, 0.6, 0.35, 0.5)
            else: return WeatherState('Drizzle', 0.5, 0.0, 0.3, 0.25, 0.0)

    def update(self, now=None):
        t = time.time() if now is None else now
        if t >= self.next_change:
            self.state = self._choose_next()
            self.next_change = t + self.rng.uniform(90, 240)

        # wind gusts
        gusting = (t < self._gust_until)
        if not gusting and self.rng.random() < 0.02*self.state.wind:
            self._gust_until = t + self.rng.uniform(3.0, 8.0)
            gusting = True

        # lightning windows during storm
        lightning = 0.0
        if self.state.name == 'Storm':
            # schedule strikes roughly per thunder_rate
            if t - self._last_strike > (60.0 / max(0.1, self.state.thunder_rate)):
                if self.rng.random() < 0.7:
                    self._lightning_until = t + self.rng.uniform(0.15, 0.5)
                    self._last_strike = t
            if t < self._lightning_until:
                lightning = 1.0

        return {
            'cloudiness': self.state.cloudiness,
            'storm': self.state.storm,
            'rain': self.state.rain,
            'wind': self.state.wind * (1.0 + (0.8 if gusting else 0.0)),
            'lightning': lightning,
            'label': self.state.name
        }
