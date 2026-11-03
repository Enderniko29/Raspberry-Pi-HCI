#!/usr/bin/env python3
"""
Brisa - a calm indoor-climate companion
Raspberry Pi + DHT22 prototype for the HCI Product Innovation Challenge.

WIRING (DHT22)
  VCC  -> 3.3V   (physical pin 1)
  DATA -> GPIO4  (physical pin 7)   * 10k pull-up to 3.3V if your sensor is a bare 4-pin part
  GND  -> GND    (physical pin 6)

INSTALL (Raspberry Pi OS Bookworm)
  sudo apt update
  sudo apt install -y python3-tk python3-pil python3-pil.imagetk libgpiod2
  pip3 install adafruit-circuitpython-dht --break-system-packages
  Optional, recommended font (designed for low vision):
    download "Atkinson Hyperlegible" from Google Fonts, unzip the .ttf files into ~/.fonts,
    then run:  fc-cache -f

RUN
  python3 raspberry.py              # full screen, real sensor
  python3 raspberry.py --windowed   # 800x480 window (handy while developing)
  python3 raspberry.py --demo       # no sensor needed, simulated readings
  python3 raspberry.py --windowed --size 1024x600   # preview how it looks on another screen

AUTO-SCALING
  The layout is designed at 800x480 and scales to any screen automatically (text, panels,
  touch areas). If the screen has a different shape, the design stays centred and the
  coloured background fills the edges. If your screen is detected wrong, pass --size.

LIVE MODE (default)
  Uses the real DHT22 only. Keyboard keys cannot change the readings. Esc quits.
  To show reactions live: breathe gently on the sensor (humidity rises -> "Damp"),
  or hold it in your closed hand for a minute (temperature rises).

KEYS (only with --demo, for practice or usability tests without the sensor)
  1 comfortable   2 damp   3 too hot   4 too dry   0 simulated normal
  n preview night mode     Esc quit
"""

import argparse
import csv
import itertools
import math
import os
import random
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from collections import deque
from datetime import datetime, timedelta

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
except ImportError:
    raise SystemExit("Pillow is missing. Run: sudo apt install -y python3-pil python3-pil.imagetk")

# ------------------------------------------------------------------ settings
W, H = 800, 480              # design size; everything is scaled from this to the real screen
SCALE, OX, OY = 1.0, 0, 0    # set at start-up by setup_scaling()
SW, SH = W, H                # real screen size in pixels
SENSOR_PIN = "D4"            # GPIO4
READ_SECONDS = 2.5           # DHT22 needs at least 2 s between reads
LOG_SECONDS = 60             # one history point per minute
STALE_SECONDS = 30           # after this, the sensor is reported as not responding
HISTORY_DAYS = 7
DATA_DIR = os.path.expanduser("~/.brisa")
HISTORY_FILE = os.path.join(DATA_DIR, "history.csv")

ROOM = "Living room"
CAREGIVER = "Ana"
CAREGIVER_REL = "daughter"

COMFORT_TEMP = (68, 78)      # deg F
HEALTHY_RH = (40, 60)        # percent
HOT_FEELS_LIKE = 90
WARM_FEELS_LIKE = 82
COOL_TEMP = 65
DRY_RH = 30
SNOOZE_MIN = 15
CHIME_EVERY_MIN = 10
CAREGIVER_AFTER_MIN = 30
NIGHT_START, NIGHT_END = 22, 7

DEMO_PRESETS = {"1": (75.0, 48.0), "2": (79.0, 64.0), "3": (93.0, 58.0), "4": (72.0, 24.0)}

# ------------------------------------------------------------------ colours
INK = "#14191D"
MUTED = "#3A4247"
TEAL = "#0D5454"
TEAL_PRESSED = "#083B3B"
TEAL_TEXT = "#0D4F4F"
GREEN = "#1E7B4A"
GREEN_TEXT = "#17492E"
AMBER_TEXT = "#7A4206"
AMBER_BODY = "#34230C"
RED = "#A01B10"
RED_TEXT = "#8F1D12"
WHITE_TINT = (255, 255, 255)
GREEN_TINT = (226, 244, 234)
AMBER_TINT = (255, 244, 226)
RED_TINT = (255, 247, 245)

# Background "ambient" colour fields. The colour of the room behind the glass IS the status.
THEMES = {
    "calm": dict(base=(228, 236, 238), shadow=(16, 42, 52), edge=(255, 255, 255, 200), hl=140, blobs=[
        ((-100, -140, 340, 300), (124, 195, 193)),
        ((480, -120, 920, 320), (166, 196, 230)),
        ((240, 240, 640, 600), (150, 210, 200))]),
    "amber": dict(base=(228, 236, 238), shadow=(60, 40, 16), edge=(255, 255, 255, 210), hl=140, blobs=[
        ((-100, -140, 340, 300), (124, 195, 193)),
        ((440, 200, 920, 680), (243, 179, 94)),
        ((280, 200, 580, 500), (166, 196, 230))]),
    "red": dict(base=(243, 226, 222), shadow=(90, 24, 16), edge=(255, 255, 255, 210), hl=140, blobs=[
        ((-120, -200, 400, 320), (226, 86, 74)),
        ((480, 220, 940, 680), (243, 154, 94)),
        ((460, -60, 740, 220), (246, 199, 184))]),
    "night": dict(base=(7, 13, 16), shadow=(0, 0, 0), edge=(255, 255, 255, 40), hl=40, blobs=[
        ((-120, -160, 300, 260), (28, 75, 90)),
        ((500, 240, 940, 680), (43, 47, 94))]),
}

ADVICE = {
    "damp": [("Turn on the AC or dehumidifier", "Brisa will watch the humidity drop."),
             ("Keep windows closed", "Humid outdoor air makes it worse."),
             ("Wipe damp spots in the bathroom", "Stops mold before it starts.")],
    "hot": [("Turn on the AC", "Or move to the coolest room in the house."),
            ("Drink a glass of water", "Heat makes it easy to get dehydrated."),
            ("Rest and avoid heavy chores", "Wait until the room is cooler.")],
    "warm": [("Turn on a fan or the AC", "Before it gets too hot."),
             ("Close curtains on sunny windows", "Keeps the afternoon sun out."),
             ("Drink a glass of water", "Small sips through the day.")],
    "dry": [("Run a humidifier", "Or set a bowl of water near a vent."),
            ("Drink a glass of water", "Dry air pulls moisture from you too."),
            ("Use lotion or eye drops if needed", "Helps with itchy skin and eyes.")],
    "cool": [("Close windows and doors", "Keeps the warm air in."),
             ("Put on a warm layer", "A sweater or blanket helps right away."),
             ("Turn up the heat a little", "Aim for at least 68°F.")],
    "sensor": [("Check the sensor cable", "Make sure the three wires are pushed in firmly."),
               ("Wait one minute", "The sensor sometimes needs a moment to respond."),
               ("Restart Brisa", "Unplug it for 10 seconds, then plug it back in.")],
}

# ------------------------------------------------------------------ helpers
FONT_FAMILY = "DejaVu Sans"
_font_cache = {}


def F(size, bold=False):
    return (FONT_FAMILY, -size, "bold" if bold else "normal")


def font_obj(size, bold=False):
    """A real (screen-pixel) font. size is already scaled."""
    key = (size, bold)
    if key not in _font_cache:
        _font_cache[key] = tkfont.Font(family=FONT_FAMILY, size=-size, weight="bold" if bold else "normal")
    return _font_cache[key]


def measure(text, size, bold=False):
    """Width of text in design (800x480) units, whatever the screen size."""
    return font_obj(max(1, round(size * SCALE)), bold).measure(text) / SCALE


def setup_scaling(screen_w, screen_h):
    """Fit the 800x480 design into the screen, keeping proportions and centring it."""
    global SCALE, OX, OY, SW, SH
    SW, SH = screen_w, screen_h
    SCALE = min(screen_w / W, screen_h / H)
    OX = round((screen_w - W * SCALE) / 2)
    OY = round((screen_h - H * SCALE) / 2)
    print(f"Screen {screen_w}x{screen_h} -> scale {SCALE:.2f}, offset ({OX}, {OY})")


class ScaledCanvas(tk.Canvas):
    """A Tk canvas you draw on in 800x480 design units; it converts to real screen pixels."""

    def _pts(self, args):
        flat = []
        for a in args:
            flat.extend(a if isinstance(a, (list, tuple)) else [a])
        return [(OX if i % 2 == 0 else OY) + v * SCALE for i, v in enumerate(flat)]

    def _kw(self, kw):
        if "width" in kw:                       # line thickness or text wrap width
            kw["width"] = kw["width"] * SCALE
        f = kw.get("font")
        if isinstance(f, tuple):
            kw["font"] = (f[0], -max(1, round(abs(f[1]) * SCALE))) + tuple(f[2:])
        return kw

    def create_line(self, *a, **kw):
        return super().create_line(*self._pts(a), **self._kw(kw))

    def create_oval(self, *a, **kw):
        return super().create_oval(*self._pts(a), **self._kw(kw))

    def create_rectangle(self, *a, **kw):
        return super().create_rectangle(*self._pts(a), **self._kw(kw))

    def create_polygon(self, *a, **kw):
        return super().create_polygon(*self._pts(a), **self._kw(kw))

    def create_text(self, *a, **kw):
        return super().create_text(*self._pts(a), **self._kw(kw))

    def screen_image(self, image, **kw):
        """Place a full-screen image at the real top-left corner (no scaling)."""
        return super().create_image(0, 0, anchor="nw", image=image, **kw)

    def cover(self, **kw):
        """A rectangle over the WHOLE screen, including any side bars."""
        return super().create_rectangle(0, 0, SW, SH, **kw)

    @staticmethod
    def to_design(x, y):
        return (x - OX) / SCALE, (y - OY) / SCALE


def heat_index(t, rh):
    """US National Weather Service 'feels like' temperature, deg F."""
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    if (simple + t) / 2 < 80:
        return simple
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh
          - 0.00683783 * t * t - 0.05481717 * rh * rh + 0.00122874 * t * t * rh
          + 0.00085282 * t * rh * rh - 0.00000199 * t * t * rh * rh)
    if rh < 13 and 80 <= t <= 112:
        hi -= ((13 - rh) / 4) * math.sqrt((17 - abs(t - 95)) / 17)
    elif rh > 85 and 80 <= t <= 87:
        hi += ((rh - 85) / 10) * ((87 - t) / 5)
    return hi


def human_duration(minutes):
    minutes = int(minutes)
    if minutes < 1:
        return "less than a minute"
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = round(minutes / 60)
    return f"{hours} hour{'s' if hours != 1 else ''}"


def human_ago(ts):
    if ts is None:
        return "never"
    s = int(time.time() - ts)
    if s < 10:
        return "just now"
    if s < 60:
        return f"{s // 10 * 10} s ago"
    return f"{s // 60} min ago"


def round_rect(canvas, x0, y0, x1, y1, r, **kw):
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, splinesteps=24, **kw)


# ------------------------------------------------------------------ data
class Model:
    """Latest reading, settings and the local-only history."""

    def __init__(self):
        self.lock = threading.Lock()
        self.temp_f = None
        self.rh = None
        self.last_ok = None
        self.damp_since = None
        self.history = deque()  # (datetime, temp_f, rh)
        self.settings = {"caregiver": True, "chime": True, "night": False}
        self.load_history()

    def update(self, reading):
        with self.lock:
            if reading is None:
                return
            t, h = reading
            if not (-40 <= t <= 176 and 0 <= h <= 100):   # reject impossible values (sensor glitch)
                return
            self.temp_f, self.rh, self.last_ok = t, h, time.time()
            if h > HEALTHY_RH[1]:
                if self.damp_since is None:
                    self.damp_since = time.time()
            else:
                self.damp_since = None

    def snapshot(self):
        with self.lock:
            return self.temp_f, self.rh, self.last_ok

    def fresh(self):
        return self.last_ok is not None and time.time() - self.last_ok < STALE_SECONDS

    def damp_minutes(self):
        return 0 if self.damp_since is None else (time.time() - self.damp_since) / 60

    # -- history (stays on this device, never uploaded)
    def load_history(self):
        cutoff = datetime.now() - timedelta(days=HISTORY_DAYS)
        try:
            with open(HISTORY_FILE, newline="") as f:
                for row in csv.reader(f):
                    try:
                        ts = datetime.fromisoformat(row[0])
                        if ts >= cutoff:
                            self.history.append((ts, float(row[1]), float(row[2])))
                    except (ValueError, IndexError):
                        continue
        except FileNotFoundError:
            pass
        # rebuild "damp since" from the most recent history
        streak_start = None
        for ts, _, h in reversed(self.history):
            if h > HEALTHY_RH[1]:
                streak_start = ts
            else:
                break
        if streak_start and (datetime.now() - self.history[-1][0]) < timedelta(minutes=5):
            self.damp_since = streak_start.timestamp()

    def log(self):
        t, h, _ = self.snapshot()
        if t is None or not self.fresh():
            return
        now = datetime.now().replace(microsecond=0)
        self.history.append((now, t, h))
        cutoff = now - timedelta(days=HISTORY_DAYS)
        while self.history and self.history[0][0] < cutoff:
            self.history.popleft()
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(HISTORY_FILE, "a", newline="") as f:
                csv.writer(f).writerow([now.isoformat(), f"{t:.1f}", f"{h:.1f}"])
        except OSError as e:
            print("Could not save history:", e)

    def delete_history(self):
        self.history.clear()
        try:
            os.remove(HISTORY_FILE)
        except FileNotFoundError:
            pass


class SensorReader(threading.Thread):
    """Reads the DHT22 in the background so the screen never freezes."""

    """Reads the DHT22 in the background so the screen never freezes.

    Live mode (default) never invents numbers: if the sensor can't be read, the screen says so.
    Simulated readings exist only when the app is started with --demo.
    """

    def __init__(self, model, demo=False):
        super().__init__(daemon=True)
        self.model = model
        self.demo = demo
        self.override = None            # demo keys only
        self.dht = None
        self.sim = [76.0, 52.0]
        self.recent = deque(maxlen=3)   # median of the last 3 good reads smooths DHT22 spikes
        self.fails = 0
        self.ok_count = 0
        self.last_init_error = None
        if not demo:
            self.open_sensor()

    def open_sensor(self):
        try:
            if self.dht is not None:
                try:
                    self.dht.exit()
                except Exception:
                    pass
            import board
            import adafruit_dht
            self.dht = adafruit_dht.DHT22(getattr(board, SENSOR_PIN), use_pulseio=False)
            self.last_init_error = None
            print(f"DHT22 ready on {SENSOR_PIN} (GPIO{SENSOR_PIN[1:]}). Waiting for the first reading...")
        except Exception as e:
            self.dht = None
            msg = str(e)
            if msg != self.last_init_error:
                print("\n*** Could not open the DHT22 sensor:", msg)
                if "No module named" in msg:
                    print("    Install it:  pip3 install adafruit-circuitpython-dht --break-system-packages")
                elif "busy" in msg.lower() or "unable to set line" in msg.lower():
                    print("    Another copy of Brisa may still be running. Close it, or run:  pkill -f raspberry.py")
                print("    Brisa will keep trying. Use --demo only for practice without the sensor.\n")
            self.last_init_error = msg

    def close(self):
        if self.dht is not None:
            try:
                self.dht.exit()
            except Exception:
                pass

    def raw_read(self):
        if self.override is not None:
            t, h = self.override
            return t + random.uniform(-0.2, 0.2), h + random.uniform(-0.4, 0.4)
        if self.demo:
            self.sim[0] = min(max(self.sim[0] + random.uniform(-0.15, 0.15), 72), 79)
            self.sim[1] = min(max(self.sim[1] + random.uniform(-0.4, 0.4), 45), 58)
            return tuple(self.sim)
        if self.dht is None:
            return None
        try:
            c, h = self.dht.temperature, self.dht.humidity
            if c is None or h is None:
                return None
            return c * 9 / 5 + 32, h
        except RuntimeError:
            return None  # normal: the DHT22 misses some reads (checksum/timing); try again next time
        except Exception as e:
            print("Sensor error:", e)
            self.open_sensor()
            return None

    def read_once(self):
        r = self.raw_read()
        if r is None:
            self.fails += 1
            if not self.demo and self.fails in (8, 30) or (self.fails and self.fails % 150 == 0):
                print(f"No reading from the DHT22 for {self.fails} tries. Check: VCC->3.3V (pin 1), "
                      f"DATA->GPIO4 (pin 7), GND (pin 6), and a 10k pull-up if it's a bare 4-pin sensor.")
            return None
        t, h = r
        if not (-40 <= t <= 176 and 0 <= h <= 100):
            return None
        if self.override is not None:          # demo keys take effect immediately
            self.recent.clear()
        self.fails = 0
        self.recent.append(r)
        self.ok_count += 1
        if self.ok_count == 1 and not self.demo:
            print(f"First reading: {t:.1f}°F, {h:.1f}% humidity. Sensor working.")
        ts = sorted(x[0] for x in self.recent)
        hs = sorted(x[1] for x in self.recent)
        return ts[len(ts) // 2], hs[len(hs) // 2]

    def run(self):
        while True:
            if not self.demo and self.dht is None:
                self.open_sensor()             # e.g. the library was installed or a stuck process ended
            self.model.update(self.read_once())
            time.sleep(READ_SECONDS)


# ------------------------------------------------------------------ status
class Status:
    def __init__(self, key, theme, label, title, body, tint, label_color):
        self.key, self.theme, self.label, self.title = key, theme, label, title
        self.body, self.tint, self.label_color = body, tint, label_color


def get_status(model):
    t, h, last_ok = model.snapshot()
    if t is None or not model.fresh():
        if t is None:
            body = "Waiting for the first reading. If this lasts more than a minute, check the sensor cable."
        else:
            body = f"Check that the sensor cable is plugged in. Showing the last reading from {human_ago(last_ok)}."
        return Status("sensor", "calm", "SENSOR PROBLEM", "Can't read the sensor", body, WHITE_TINT, RED_TEXT)
    fl = heat_index(t, h)
    if fl >= HOT_FEELS_LIKE:
        return Status("hot", "red", "HEAT WARNING", "Too hot inside",
                      f"It feels like {fl:.0f}°F. Heat can be dangerous even indoors.", RED_TINT, RED_TEXT)
    if h > HEALTHY_RH[1]:
        mins = model.damp_minutes()
        if mins < 5:
            body = "Humidity is above 60%. If it stays this way, mold can start to grow."
        else:
            body = f"It has been this damp for {human_duration(mins)}. If it stays this way, mold can start to grow."
        title = "Warm and damp" if t > COMFORT_TEMP[1] else "Damp air"
        return Status("damp", "amber", "NEEDS ATTENTION", title, body, AMBER_TINT, AMBER_TEXT)
    if h < DRY_RH:
        return Status("dry", "amber", "NEEDS ATTENTION", "Very dry air",
                      "Dry air can irritate your eyes, skin and breathing.", AMBER_TINT, AMBER_TEXT)
    if t < COOL_TEMP:
        return Status("cool", "amber", "NEEDS ATTENTION", "Chilly inside",
                      f"It's {t:.0f}°F. A warm layer or a little heat will help.", AMBER_TINT, AMBER_TEXT)
    if fl >= WARM_FEELS_LIKE:
        return Status("warm", "amber", "NEEDS ATTENTION", "Getting warm",
                      f"It feels like {fl:.0f}°F. A fan or the AC will help before it gets too hot.",
                      AMBER_TINT, AMBER_TEXT)
    return Status("ok", "calm", "ALL GOOD", "Comfortable",
                  "Temperature and humidity are in a healthy range. Nothing to do.", GREEN_TINT, GREEN_TEXT)


# ------------------------------------------------------------------ glass rendering
_blob_cache = {}


def blob_layer(theme):
    """The soft colour field, filling the whole real screen (side bars included)."""
    if theme not in _blob_cache:
        th = THEMES[theme]
        # Draw at design resolution over the full screen area, then upscale: it is so blurry
        # that upscaling is invisible, and it keeps start-up fast on a Pi.
        lw, lh = math.ceil(SW / SCALE), math.ceil(SH / SCALE)
        dx, dy = OX / SCALE, OY / SCALE
        img = Image.new("RGB", (lw, lh), th["base"])
        for (x0, y0, x1, y1), color in th["blobs"]:
            mask = Image.new("L", (lw, lh), 0)
            ImageDraw.Draw(mask).ellipse((x0 + dx, y0 + dy, x1 + dx, y1 + dy), fill=220)
            img.paste(color, mask=mask)
        img = img.filter(ImageFilter.GaussianBlur(70))
        _blob_cache[theme] = img.resize((SW, SH), Image.BILINEAR)
    return _blob_cache[theme]


def compose_glass(theme, panels):
    """panels: list of (box, radius, tint_rgb, alpha) in design units. Returns a full-screen PIL image."""
    th = THEMES[theme]
    s = SCALE

    def P(box):
        x0, y0, x1, y1 = box
        return (round(OX + x0 * s), round(OY + y0 * s), round(OX + x1 * s), round(OY + y1 * s))

    img = blob_layer(theme).copy()
    if panels:
        shadow = Image.new("L", (SW, SH), 0)
        sd = ImageDraw.Draw(shadow)
        for (x0, y0, x1, y1), r, _, _ in panels:
            sd.rounded_rectangle(P((x0 + 2, y0 + 8, x1 - 2, y1 + 8)), r * s, fill=55)
        shadow = shadow.filter(ImageFilter.GaussianBlur(12 * s))
        img = Image.composite(Image.new("RGB", (SW, SH), th["shadow"]), img, shadow)
    line = max(1, round(s))
    for box, r, tint, alpha in panels:
        x0, y0, x1, y1 = pb = P(box)
        rr = r * s
        region = img.crop(pb).filter(ImageFilter.GaussianBlur(14 * s)).convert("RGBA")
        region = Image.alpha_composite(region, Image.new("RGBA", region.size, tuple(tint) + (alpha,)))
        mask = Image.new("L", region.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, region.size[0] - 1, region.size[1] - 1), rr, fill=255)
        img.paste(region.convert("RGB"), (x0, y0), mask)
        d = ImageDraw.Draw(img, "RGBA")
        d.rounded_rectangle(pb, rr, outline=th["edge"], width=line)
        d.line((x0 + rr, y0 + line, x1 - rr, y0 + line), fill=(255, 255, 255, th["hl"]), width=line)
    return img


# ------------------------------------------------------------------ app
BUTTON_STYLES = {
    #              fill       pressed    text        outline
    "primary":   (TEAL,      TEAL_PRESSED, "#FFFFFF", TEAL),
    "secondary": ("#F7FAFA", "#DCE7E7",  TEAL_TEXT,  "#FFFFFF"),
    "neutral":   ("#F7FAFA", "#DCE7E7",  INK,        "#FFFFFF"),
    "danger":    ("#FFFFFF", "#F7DEDB",  RED_TEXT,   "#C46A61"),
    "danger_solid": (RED,    "#7E150C",  "#FFFFFF",  RED),
}


class BrisaApp:
    def __init__(self, root, model, sensor, windowed, hide_cursor=False):
        self.root, self.model, self.sensor = root, model, sensor
        root.title("Brisa")
        root.configure(bg="black")
        if windowed:
            root.geometry(f"{SW}x{SH}")
            root.resizable(False, False)
        else:
            root.attributes("-fullscreen", True)
        self.c = ScaledCanvas(root, width=SW, height=SH, highlightthickness=0, bd=0, bg="black",
                              cursor="none" if hide_cursor else "arrow")
        self.c.place(x=0, y=0)

        # One click handler for the whole screen. Buttons register their area on every redraw,
        # so a click still works even if the screen refreshes while the button is held down.
        self.hits = []                 # (box, command, shape_id, normal_fill, pressed_fill)
        self.pressed = None            # (hit, time)
        self.c.bind("<ButtonPress-1>", self.on_press)
        self.c.bind("<ButtonRelease-1>", self.on_release)
        root.focus_force()

        self.screen = "home"
        self.history_view = "today"
        self.confirm_delete = False
        self.done = set()
        self.done_for = None
        self.toast = None              # (text, until)
        self.snooze_until = 0
        self.alert_started = None
        self.last_chime = 0
        self.caregiver_sent = False
        self.wake_until = 0
        self.force_night = False
        self._ids = itertools.count()
        self._photo_cache = {}
        self._photo_refs = []
        self._sig = None

        root.bind("<Escape>", lambda e: root.destroy())
        root.bind("<Key>", self.on_key)
        self.tick()
        root.after(5000, self.log_tick)

    # ---------------------------------------------------------- loop
    def on_key(self, e):
        if not self.sensor.demo:       # live mode: keys can never fake a reading
            return
        if e.char in DEMO_PRESETS:
            self.sensor.override = DEMO_PRESETS[e.char]
            self.model.update(self.sensor.read_once())
            self.show_toast("Demo reading applied")
        elif e.char == "0":
            self.sensor.override = None
            self.show_toast("Live sensor")
        elif e.char == "n":
            self.force_night = not self.force_night
            self.wake_until = 0
        self.tick(reschedule=False)

    def log_tick(self):
        self.model.log()
        self.root.after(LOG_SECONDS * 1000, self.log_tick)

    def night_active(self):
        if self.screen != "home" or time.time() < self.wake_until:
            return False
        hour = datetime.now().hour
        scheduled = self.model.settings["night"] and (hour >= NIGHT_START or hour < NIGHT_END)
        return self.force_night or scheduled

    def tick(self, reschedule=True):
        now = time.time()
        st = get_status(self.model)

        if st.key != self.done_for:            # new situation, fresh checklist
            self.done, self.done_for = set(), st.key

        if st.key == "hot":
            if self.alert_started is None:
                self.alert_started = now
            if now >= self.snooze_until and self.screen != "alert":
                self.go("alert", redraw=False)
            if self.screen == "alert" and self.model.settings["chime"] and now - self.last_chime >= CHIME_EVERY_MIN * 60:
                self.root.bell()
                self.last_chime = now
            if (self.model.settings["caregiver"] and not self.caregiver_sent
                    and now - self.alert_started >= CAREGIVER_AFTER_MIN * 60):
                self.caregiver_sent = True
                print(f"[prototype] Would send a text to {CAREGIVER}: heat alert unanswered for 30 min.")
        else:
            if self.screen == "alert":
                self.go("home", redraw=False)
                self.show_toast("It's cooler now. Nice work.", redraw=False)
            self.alert_started, self.caregiver_sent, self.last_chime = None, False, 0

        if self.toast and now > self.toast[1]:
            self.toast = None

        t, h, last_ok = self.model.snapshot()
        shown = "night" if self.night_active() else self.screen
        sig = (shown, st.key, st.body, None if t is None else round(t), None if h is None else round(h),
               self.model.fresh(), human_ago(last_ok), tuple(sorted(self.done)), tuple(self.model.settings.values()),
               self.history_view, self.confirm_delete, self.toast and self.toast[0], self.caregiver_sent,
               datetime.now().strftime("%H:%M") if shown == "night" else None,
               len(self.model.history) if shown == "history" else None)
        holding = self.pressed is not None and now - self.pressed[1] < 2
        if sig != self._sig and not holding:   # never redraw under a finger that is still pressing
            self._sig = sig
            self.draw(shown, st)
        if reschedule:
            self.root.after(1000, self.tick)

    def go(self, screen, redraw=True):
        self.screen = screen
        self.confirm_delete = False
        if redraw:
            self._sig = None
            self.tick(reschedule=False)

    def show_toast(self, text, seconds=3, redraw=True):
        self.toast = (text, time.time() + seconds)
        if redraw:
            self.tick(reschedule=False)

    # ---------------------------------------------------------- drawing primitives
    def background(self, theme, panels):
        key = (theme, tuple(panels))
        photo = self._photo_cache.get(key)
        if photo is None:
            if len(self._photo_cache) > 40:
                self._photo_cache.clear()
            photo = ImageTk.PhotoImage(compose_glass(theme, panels))
            self._photo_cache[key] = photo
        self._photo_refs = [photo]
        return self.c.screen_image(photo, tags=("bg",))

    # ---- clicks / taps
    TOUCH_SLOP = 12                    # design px of forgiveness around buttons for fingers

    def hit_at(self, x, y, slop=0):
        for hit in reversed(self.hits):            # topmost (last drawn) first
            x0, y0, x1, y1 = hit[0]
            if x0 - slop <= x <= x1 + slop and y0 - slop <= y <= y1 + slop:
                return hit
        return None

    def on_press(self, e):
        x, y = self.c.to_design(e.x, e.y)
        hit = self.hit_at(x, y) or self.hit_at(x, y, self.TOUCH_SLOP)
        self.pressed = (hit, time.time()) if hit else None
        if hit and hit[2] is not None:
            try:
                self.c.itemconfigure(hit[2], fill=hit[4])
            except tk.TclError:
                pass

    def on_release(self, e):
        pressed, self.pressed = self.pressed, None
        if not pressed:
            return
        hit = pressed[0]
        if hit[2] is not None:
            try:
                self.c.itemconfigure(hit[2], fill=hit[3])
            except tk.TclError:
                pass
        x, y = self.c.to_design(e.x, e.y)
        x0, y0, x1, y1 = hit[0]
        s = self.TOUCH_SLOP * 2
        if hit[1] and x0 - s <= x <= x1 + s and y0 - s <= y <= y1 + s:
            hit[1]()

    def add_hit(self, box, command, shape=None, fill=None, pressed=None):
        self.hits.append((box, command, shape, fill, pressed))

    def button(self, box, text, command, style="primary", size=20):
        x0, y0, x1, y1 = box
        fill, pressed, fg, outline = BUTTON_STYLES[style]
        shape = round_rect(self.c, x0, y0, x1, y1, (y1 - y0) / 2, fill=fill, outline=outline, width=1.5)
        self.c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=text, fill=fg, font=F(size, True))
        self.add_hit(box, command, shape, fill, pressed)

    def switch(self, box, on, command):
        x0, y0, x1, y1 = box
        round_rect(self.c, x0, y0, x1, y1, (y1 - y0) / 2, fill=GREEN if on else "#B7C0C3", outline="")
        d = (y1 - y0) - 8
        kx = x1 - 4 - d if on else x0 + 4
        self.c.create_oval(kx, y0 + 4, kx + d, y0 + 4 + d, fill="#FFFFFF", outline="")
        self.add_hit(box, command)

    def back_button(self):
        self.button((28, 22, 140, 74), "‹ Back", lambda: self.go("home"), "secondary", 18)

    def text(self, x, y, s, size, bold=False, fill=INK, **kw):
        return self.c.create_text(x, y, text=s, font=F(size, bold), fill=fill, **kw)

    # ---------------------------------------------------------- screens
    def draw(self, shown, st):
        self.c.delete("all")
        self.hits = []
        getattr(self, f"draw_{shown}")(st)
        if self.toast:
            msg = self.toast[0]
            w = measure(msg, 17, True) + 48
            round_rect(self.c, (W - w) / 2, 410, (W + w) / 2, 458, 24, fill="#1F2A2E", outline="")
            self.text(W / 2, 434, msg, 17, True, "#FFFFFF")

    # 1 - Home
    def draw_home(self, st):
        t, h, last_ok = self.model.snapshot()
        self.background(st.theme, [
            ((596, 22, 772, 66), 22, WHITE_TINT, 130),
            ((28, 88, 392, 230), 26, WHITE_TINT, 150),
            ((28, 242, 392, 384), 26, WHITE_TINT, 150),
            ((408, 88, 772, 384), 28, st.tint, 175),
        ])
        self.text(28, 22, ROOM, 22, True, anchor="nw")
        fresh = self.model.fresh()
        self.c.create_oval(28, 56, 37, 65, fill=GREEN if fresh else RED, outline="")
        if self.sensor.demo:
            sub = "DEMO MODE · simulated readings"
        elif fresh:
            sub = f"Live sensor · updated {human_ago(last_ok)}"
        elif last_ok is None:
            sub = "Connecting to the sensor…"
        else:
            sub = "Sensor not responding"
        self.text(45, 61, sub, 15, fill=MUTED, anchor="w")
        self.text(684, 44, "No mic · No camera", 15, True, "#2A3136")

        if t is None:
            self.reading_card(28, 88, "Temperature", "--", "", MUTED, None, (55, 95), COMFORT_TEMP,
                              "Comfortable: 68–78°F")
            self.reading_card(28, 242, "Humidity", "--", "", MUTED, None, (0, 100), HEALTHY_RH, "Healthy: 40–60%")
        else:
            fl = heat_index(t, h)
            ttag, tcol = ("", MUTED)
            if fl - t >= 2:
                ttag, tcol = f"Feels like {fl:.0f}°", AMBER_TEXT if fl > COMFORT_TEMP[1] else MUTED
            elif t < COOL_TEMP:
                ttag, tcol = "Chilly", AMBER_TEXT
            elif COMFORT_TEMP[0] <= t <= COMFORT_TEMP[1]:
                ttag, tcol = "Comfortable", GREEN_TEXT
            if h > HEALTHY_RH[1]:
                htag, hcol = "Damp", AMBER_TEXT
            elif h < DRY_RH:
                htag, hcol = "Dry", AMBER_TEXT
            elif HEALTHY_RH[0] <= h <= HEALTHY_RH[1]:
                htag, hcol = "Healthy", GREEN_TEXT
            else:
                htag, hcol = "A bit dry", MUTED
            self.reading_card(28, 88, "Temperature", f"{t:.0f}°F", ttag, tcol, t, (55, 95), COMFORT_TEMP,
                              "Comfortable: 68–78°F")
            self.reading_card(28, 242, "Humidity", f"{h:.0f}%", htag, hcol, h, (0, 100), HEALTHY_RH,
                              "Healthy: 40–60%")

        # status panel
        self.c.create_oval(430, 110, 444, 124, fill=st.label_color, outline="")
        self.text(454, 117, st.label, 15, True, st.label_color, anchor="w")
        self.text(430, 136, st.title, 30, True, anchor="nw")
        self.text(430, 182, st.body, 19, fill=AMBER_BODY if st.theme == "amber" else INK, anchor="nw", width=322)
        if st.key == "ok":
            self.button((428, 312, 752, 368), "See tips anyway", lambda: self.go("advice"), "secondary", 21)
        else:
            label = "How do I fix it?" if st.key == "sensor" else "What should I do?"
            self.button((428, 312, 752, 368), label + "  ›", lambda: self.go("advice"), "primary", 22)

        self.button((28, 398, 392, 450), "History", lambda: self.go("history"), "secondary", 18)
        self.button((408, 398, 772, 450), "Privacy & settings", lambda: self.go("privacy"), "secondary", 18)

    def reading_card(self, x, y, label, value, tag, tagcol, v, scale, band, range_text):
        self.text(x + 22, y + 14, label, 16, fill=MUTED, anchor="nw")
        self.text(x + 22, y + 34, value, 52, True, anchor="nw")
        if tag:
            vw = measure(value, 52, True)
            self.text(x + 22 + vw + 12, y + 84, tag, 17, True, tagcol, anchor="sw")
        tx0, tx1, ty = x + 22, x + 342, y + 104

        def px(val):
            return tx0 + (tx1 - tx0) * min(max((val - scale[0]) / (scale[1] - scale[0]), 0), 1)

        round_rect(self.c, tx0, ty, tx1, ty + 10, 5, fill="#D3DBDD", outline="")
        round_rect(self.c, px(band[0]), ty, px(band[1]), ty + 10, 5, fill="#A9D3BC", outline="")
        if v is not None:
            mx = px(v)
            self.c.create_rectangle(mx - 2, ty - 4, mx + 2, ty + 14, fill=INK, outline="")
        self.text(x + 22, y + 120, range_text, 13, fill=MUTED, anchor="nw")

    # 2 - Advice
    def draw_advice(self, st):
        items = ADVICE.get(st.key)
        if not items:
            self.background("calm", [((28, 88, 772, 250), 28, GREEN_TINT, 170)])
            self.back_button()
            self.text(160, 48, "Tips", 28, True, anchor="w")
            self.text(56, 116, "All good right now", 28, True, anchor="nw")
            self.text(56, 162, "Your home is comfortable. Brisa keeps watching and will let you know "
                               "if anything changes — no need to check.", 19, fill=GREEN_TEXT, anchor="nw",
                      width=680)
            return
        panels = []
        y = 88
        for i in range(len(items)):
            done = i in self.done
            panels.append(((28, y, 772, y + 76), 26, GREEN_TINT if done else WHITE_TINT, 180 if done else 150))
            y += 86
        panels.append(((28, 356, 772, 408), 26, WHITE_TINT, 115))
        self.background("red" if st.key == "hot" else "calm", panels)
        self.back_button()
        self.text(160, 48, "What to do now", 28, True, anchor="w")

        y = 88
        for i, (title, sub) in enumerate(items):
            done = i in self.done
            cx, cy = 66, y + 38
            if done:
                self.c.create_oval(cx - 20, cy - 20, cx + 20, cy + 20, fill=GREEN, outline="")
                self.c.create_line(cx - 9, cy, cx - 3, cy + 7, cx + 10, cy - 7, fill="#FFFFFF", width=3,
                                   capstyle="round", joinstyle="round")
            else:
                self.c.create_oval(cx - 19, cy - 19, cx + 19, cy + 19, outline=MUTED, width=2.5)
                self.text(cx, cy, str(i + 1), 19, True, "#2A3136")
            self.text(100, y + 16, title, 21, True, anchor="nw")
            self.text(100, y + 46, "Done. " + sub if done else sub, 16, fill=GREEN_TEXT if done else MUTED,
                      anchor="nw")

            def toggle(i=i):
                self.done ^= {i}
                self.tick(reschedule=False)

            if done:
                self.button((650, y + 12, 756, y + 64), "Undo", toggle, "secondary", 17)
            else:
                self.button((650, y + 12, 756, y + 64), "Done", toggle, "primary", 17)
            y += 86

        n = len(self.done)
        if n == len(items):
            msg = "All done — thank you. Brisa will keep watching and tell you when it's better."
        else:
            msg = f"{n} of {len(items)} done. Brisa will check again in 30 minutes and tell you if things are better."
        self.text(52, 382, msg, 16, fill="#123838", anchor="w", width=700)

    # 3 - Heat alert
    def draw_alert(self, st):
        t, h, _ = self.model.snapshot()
        fl = heat_index(t, h) if t is not None else 0
        self.background("red", [((28, 86, 772, 460), 30, RED_TINT, 185)])
        round_rect(self.c, 28, 20, 316, 68, 24, fill=RED, outline="")
        self.c.create_polygon(50, 58, 62, 34, 74, 58, fill="", outline="#FFFFFF", width=2.5, joinstyle="round")
        self.c.create_line(62, 43, 62, 50, fill="#FFFFFF", width=2.5)
        self.text(84, 44, "TOO HOT INSIDE", 22, True, "#FFFFFF", anchor="w")

        self.text(56, 104, f"Feels like {fl:.0f}°F", 56, True, anchor="nw")
        self.text(56, 172, f"It's {t:.0f}°F with {h:.0f}% humidity. Heat can be dangerous even indoors. "
                           "Turn on the AC or move to a cooler room.", 20, fill="#44160F", anchor="nw", width=690)

        def cooling():
            self.snooze_until = time.time() + SNOOZE_MIN * 60
            self.alert_started = time.time()
            self.go("advice", redraw=False)
            self.show_toast(f"Thank you. Brisa will check again in {SNOOZE_MIN} minutes.")

        def call():
            self.show_toast("Calling isn't connected in this prototype.")

        def remind():
            self.snooze_until = time.time() + SNOOZE_MIN * 60
            self.go("home", redraw=False)
            self.show_toast(f"OK. Brisa will remind you in {SNOOZE_MIN} minutes.")

        self.button((56, 250, 744, 318), "✓  I'm cooling down now", cooling, "primary", 24)
        self.button((56, 330, 394, 386), f"Call {CAREGIVER} ({CAREGIVER_REL})", call, "secondary", 19)
        self.button((406, 330, 744, 386), f"Remind me in {SNOOZE_MIN} min", remind, "neutral", 19)

        notes = []
        if self.model.settings["chime"]:
            notes.append(f"A soft chime repeats every {CHIME_EVERY_MIN} minutes until the room cools.")
        if self.caregiver_sent:
            notes.append(f"{CAREGIVER} has been sent a text (simulated in this prototype).")
        elif self.model.settings["caregiver"]:
            notes.append(f"If no one responds in {CAREGIVER_AFTER_MIN} minutes, {CAREGIVER} gets a text.")
        if notes:
            self.text(56, 400, " ".join(notes), 15, fill="#4E2620", anchor="nw", width=690)

    # 4 - History
    def draw_history(self, st):
        self.background("calm", [
            ((556, 22, 772, 74), 26, WHITE_TINT, 115),
            ((28, 88, 772, 318), 28, WHITE_TINT, 150),
            ((28, 330, 266, 412), 22, AMBER_TINT, 165),
            ((281, 330, 519, 412), 22, AMBER_TINT, 165),
            ((534, 330, 772, 412), 22, WHITE_TINT, 150),
        ])
        self.back_button()
        self.text(160, 48, "Humidity history", 28, True, anchor="w")

        def set_view(v):
            self.history_view = v
            self.tick(reschedule=False)

        self.button((560, 26, 652, 70), "Today", lambda: set_view("today"),
                    "primary" if self.history_view == "today" else "secondary", 17)
        self.button((656, 26, 768, 70), "This week", lambda: set_view("week"),
                    "primary" if self.history_view == "week" else "secondary", 17)

        now = datetime.now()
        hist = list(self.model.history)
        if self.history_view == "today":
            self.draw_day_chart(hist, now)
        else:
            self.draw_week_chart(hist, now)

        today = [r for r in hist if r[0].date() == now.date()]
        damp_today = sum(1 for r in today if r[2] > HEALTHY_RH[1])
        week_start = now.date() - timedelta(days=6)
        damp_days = len({r[0].date() for r in hist if r[0].date() >= week_start and r[2] > HEALTHY_RH[1]})
        cards = [
            (human_duration(damp_today) if today else "—", "too damp today"),
            (f"{damp_days} of 7 days" if hist else "—", "had damp air this week"),
        ]
        if today:
            hottest = max(today, key=lambda r: r[1])
            cards.append((f"{hottest[1]:.0f}°F at {hottest[0].strftime('%-I %p')}", "warmest today"))
        else:
            cards.append(("—", "warmest today"))
        for (value, label), x in zip(cards, (28, 281, 534)):
            self.text(x + 18, y_top := 344, value, 22, True, anchor="nw")
            self.text(x + 18, y_top + 34, label, 15, fill=MUTED, anchor="nw")

    def draw_day_chart(self, hist, now):
        px0, px1, py0, py1 = 52, 748, 108, 288
        start = now - timedelta(hours=24)
        pts = [r for r in hist if r[0] >= start]

        def ymap(v):
            return py1 - (min(max(v, 30), 80) - 30) / 50 * (py1 - py0)

        def xmap(ts):
            return px0 + (ts - start).total_seconds() / 86400 * (px1 - px0)

        self.c.create_rectangle(px0, ymap(60), px1, ymap(40), fill="#CFE6DA", outline="")
        self.text(px1 - 8, ymap(60) + 14, "Healthy 40–60%", 13, fill=GREEN_TEXT, anchor="e")
        for label, x, anchor in (("24 h ago", px0, "w"), ("12 h ago", (px0 + px1) / 2, "center"),
                                 ("Now", px1, "e")):
            self.text(x, 304, label, 13, fill=MUTED, anchor=anchor)
        if len(pts) < 2:
            self.text(400, 198, "Collecting data… Brisa saves one reading every minute,\n"
                                "so the graph fills in over the day.", 18, fill=MUTED, justify="center")
            return
        coords = []
        for ts, _, h in pts:
            coords += [xmap(ts), ymap(h)]
        self.c.create_line(*coords, fill=TEAL, width=4, capstyle="round", joinstyle="round", smooth=True)
        lx, ly = coords[-2], coords[-1]
        last_h = pts[-1][2]
        col = AMBER_TEXT if last_h > HEALTHY_RH[1] or last_h < DRY_RH else GREEN
        self.c.create_oval(lx - 8, ly - 8, lx + 8, ly + 8, fill=col, outline="#FFFFFF", width=2)
        self.text(lx - 12, ly - 18, f"Now {last_h:.0f}%", 15, True, col, anchor="e")

    def draw_week_chart(self, hist, now):
        self.text(52, 108, "Hours with damp air (above 60%)", 16, True, MUTED, anchor="nw")
        days = [now.date() - timedelta(days=i) for i in range(6, -1, -1)]
        hours = [sum(1 for r in hist if r[0].date() == d and r[2] > HEALTHY_RH[1]) / 60 for d in days]
        top = max(max(hours), 1)
        bx0, bw, gap, base, maxh = 70, 70, 26, 280, 130
        for i, (d, hr) in enumerate(zip(days, hours)):
            x = bx0 + i * (bw + gap)
            bh = max(hr / top * maxh, 3)
            round_rect(self.c, x, base - bh, x + bw, base, 10,
                       fill="#E2A65C" if hr >= 1 else "#BFD3D6", outline="")
            self.text(x + bw / 2, base - bh - 12, f"{hr:.0f} h" if hr >= 1 else "", 14, True, AMBER_TEXT)
            self.text(x + bw / 2, 300, "Today" if d == now.date() else d.strftime("%a"), 14, fill=MUTED)

    # 5 - Privacy & settings
    def draw_privacy(self, st):
        self.background("calm", [((28, 88, 392, 388), 28, WHITE_TINT, 150),
                                 ((408, 88, 772, 388), 28, WHITE_TINT, 150)])
        self.back_button()
        self.text(160, 48, "Privacy & settings", 28, True, anchor="w")

        self.text(50, 108, "What Brisa knows", 20, True, anchor="nw")
        knows = [("Temperature & humidity", f"Stays on this device. Kept {HISTORY_DAYS} days."),
                 ("No microphone", "Not built in. Brisa can't listen."),
                 ("No camera", "Not built in. Brisa can't see you.")]
        for i, (title, sub) in enumerate(knows):
            y = 156 + i * 74
            self.c.create_oval(50, y + 4, 62, y + 16, fill=TEAL, outline="")
            self.text(74, y, title, 17, True, anchor="nw")
            self.text(74, y + 26, sub, 15, fill=MUTED, anchor="nw")

        self.text(430, 108, "You decide", 20, True, anchor="nw")
        options = [("caregiver", f"Text {CAREGIVER} in a heat alert", f"Only if you don't respond in {CAREGIVER_AFTER_MIN} min"),
                   ("chime", "Soft chime for alerts", "Screen alert only when off"),
                   ("night", "Night mode", "Dim screen 10 PM – 7 AM")]
        for i, (key, title, sub) in enumerate(options):
            y = 152 + i * 74

            def flip(key=key):
                self.model.settings[key] = not self.model.settings[key]
                self.show_toast("Saved")

            self.text(430, y + 2, title, 17, True, anchor="nw", width=236)
            self.text(430, y + 28, sub, 14, fill=MUTED, anchor="nw", width=236)
            self.switch((680, y, 752, y + 44), self.model.settings[key], flip)

        self.button((28, 402, 262, 454), "Delete all history…", self.ask_delete, "danger", 17)
        self.text(280, 428, "Asks you to confirm first. Readings never leave this device; only alert texts "
                            "you turn on are sent.", 14, fill="#2A3136", anchor="w", width=490)

        if self.confirm_delete:
            self.c.cover(fill="#000000", stipple="gray50", outline="")
            self.add_hit((-10000, -10000, 10000, 10000), None)   # block clicks to the screen behind the dialog
            round_rect(self.c, 170, 120, 630, 360, 28, fill="#FFFFFF", outline="")
            self.text(400, 166, "Delete all history?", 26, True)
            self.text(400, 220, "This removes every saved reading from this device. It can't be undone.",
                      17, fill=MUTED, width=380, justify="center")
            self.button((200, 280, 392, 334), "Cancel", self.cancel_delete, "neutral", 19)
            self.button((408, 280, 600, 334), "Delete", self.do_delete, "danger_solid", 19)

    def ask_delete(self):
        self.confirm_delete = True
        self.tick(reschedule=False)

    def cancel_delete(self):
        self.confirm_delete = False
        self.tick(reschedule=False)

    def do_delete(self):
        self.model.delete_history()
        self.confirm_delete = False
        self.show_toast("History deleted")

    # 6 - Night
    def draw_night(self, st):
        t, h, _ = self.model.snapshot()
        self.background("night", [((170, 100, 630, 330), 36, WHITE_TINT, 16)])
        reading = "-- · --" if t is None else f"{t:.0f}° · {h:.0f}%"
        self.text(400, 146, datetime.now().strftime("%-I:%M %p"), 22, fill="#9FB3B7")
        self.text(400, 214, reading, 84, True, "#E3ECEE")
        good = st.key == "ok"
        line = "Comfortable for sleep" if good else f"{st.title} — tap for details"
        col = "#A9D8BE" if good else "#F2C38A"
        w = measure(line, 22)
        self.c.create_oval(400 - w / 2 - 22, 278, 400 - w / 2 - 10, 290, fill=col, outline="")
        self.text(400 + 0, 284, line, 22, fill=col)
        self.text(400, 390, "Tap anywhere to wake the screen", 17, fill="#8FA3A7")

        def wake():
            self.wake_until = time.time() + 120
            self.force_night = False
            self._sig = None
            self.tick(reschedule=False)

        self.add_hit((-10000, -10000, 10000, 10000), wake)    # the whole screen, side bars included


# ------------------------------------------------------------------ main
def main():
    global FONT_FAMILY
    ap = argparse.ArgumentParser(description="Brisa indoor-climate companion")
    ap.add_argument("--demo", action="store_true", help="simulate the sensor")
    ap.add_argument("--windowed", action="store_true", help="run in a window instead of full screen")
    ap.add_argument("--size", metavar="WxH",
                    help="force a screen size, e.g. 1024x600 (default: detect the screen; 800x480 when windowed)")
    ap.add_argument("--hide-cursor", action="store_true",
                    help="hide the mouse pointer (only for touch screens with no mouse)")
    args = ap.parse_args()

    root = tk.Tk()
    if args.size:
        try:
            sw, sh = (int(v) for v in args.size.lower().split("x"))
        except ValueError:
            raise SystemExit("--size must look like 1024x600")
    elif args.windowed:
        sw, sh = W, H
    else:
        root.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    setup_scaling(sw, sh)
    families = set(tkfont.families(root))
    for fam in ("Atkinson Hyperlegible", "Atkinson Hyperlegible Next", "Inter", "DejaVu Sans", "Helvetica"):
        if fam in families:
            FONT_FAMILY = fam
            break
    print("Font:", FONT_FAMILY)

    model = Model()
    sensor = SensorReader(model, demo=args.demo)
    sensor.start()
    BrisaApp(root, model, sensor, windowed=args.windowed, hide_cursor=args.hide_cursor)
    if sensor.demo:
        print("DEMO MODE. Keys: 1 comfortable | 2 damp | 3 hot | 4 dry | 0 normal | n night | Esc quit")
    else:
        print("LIVE MODE: real DHT22 readings only. Press Esc to quit.")
    try:
        root.mainloop()
    finally:
        sensor.close()


if __name__ == "__main__":
    main()
