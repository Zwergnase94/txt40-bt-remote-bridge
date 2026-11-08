#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
omni_bt_remote.py - Omniwheel 1-Stick Steuerung (Linker Joystick) fuer TXT 4.0

Steuert das 4-Rad Omniwheel-Fahrzeug komplett ueber den LINKEN Joystick der
blauen fischertechnik Bluetooth-Fernsteuerung (via ESP32 I2C-Bridge 0x42):
  - Vorwaerts / Rueckwaerts
  - Seitwaerts Links / Rechts
  - Schraege Diagonalen (Vorne-Links, Vorne-Rechts, Hinten-Links, Hinten-Rechts)
  - Stufenlose Geschwindigkeitsregelung je nach Joystick-Ausschlag (0..512)
  - Umschaltbar zwischen:
      1. "8-Wege Sync" (Standard / Kanal 1 blau): Exakte 8 Richtungen + Diagonalen
         mit voller Encoder-Synchronisation.
      2. "360° Frei" (Kanal 2 orange / per Display-Button): Stufenloser Zwischenwinkel.
"""

import atexit
import math
import os
import signal
import sys
import time

ws_dir = os.path.dirname(os.path.abspath(__file__))
if ws_dir not in sys.path:
    sys.path.insert(0, ws_dir)
lib_dir = os.path.join(ws_dir, "lib")
if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

from ft_bt_remote import FtBtRemote

# Optionales Touch-Display GUI laden (funktioniert auch ohne Display z.B. per SSH)
has_gui = False
display = None
try:
    from lib.display import display as _disp
    display = _disp
    has_gui = True
except Exception:
    has_gui = False

import fischertechnik.factories as txt_factory
from fischertechnik.controller.Motor import Motor

txt_factory.init()
txt_factory.init_motor_factory()
txt_factory.init_counter_factory()

TXT_M = txt_factory.controller_factory.create_graphical_controller()
M1 = txt_factory.motor_factory.create_encodermotor(TXT_M, 1)
M2 = txt_factory.motor_factory.create_encodermotor(TXT_M, 2)
M3 = txt_factory.motor_factory.create_encodermotor(TXT_M, 3)
M4 = txt_factory.motor_factory.create_encodermotor(TXT_M, 4)
txt_factory.initialized()

# Steuer-Konfiguration
MAX_SPEED = 512
MIN_SPEED = 120
DEADZONE_PCT = 12

# Modus: False = 8-Wege Encoder-Sync (Standard), True = 360-Grad Freiwinkel
free_360_mode = False
last_remote_channel = 0
last_drive_cmd = None


def stop_motors():
    global last_drive_cmd
    if last_drive_cmd == ("STOP", 0, 0):
        return
    last_drive_cmd = ("STOP", 0, 0)
    try:
        M1.stop_sync(M2, M3, M4)
    except Exception:
        for m in (M1, M2, M3, M4):
            try:
                m.stop()
            except Exception:
                pass


def apply_8way_motion(dir_name: str, speed: int):
    """Steuert die 4 Omniwheels exakt synchronisiert in eine der 8 Richtungen."""
    global last_drive_cmd
    # Geschwindigkeit in 16er-Stufen runden, damit der Encoder-PID ruhig laeuft
    q_speed = 0 if speed < MIN_SPEED else min(MAX_SPEED, int(round(speed / 16.0) * 16))
    if q_speed == 0 or dir_name == "STOP":
        stop_motors()
        return

    cmd_key = (dir_name, q_speed, 0)
    if cmd_key == last_drive_cmd:
        return
    last_drive_cmd = cmd_key

    if dir_name == "VORNE":
        M1.set_speed(q_speed, Motor.CCW)
        M2.set_speed(q_speed, Motor.CCW)
        M3.set_speed(q_speed, Motor.CCW)
        M4.set_speed(q_speed, Motor.CCW)
        M1.start_sync(M2, M3, M4)

    elif dir_name == "HINTEN":
        M1.set_speed(q_speed, Motor.CW)
        M2.set_speed(q_speed, Motor.CW)
        M3.set_speed(q_speed, Motor.CW)
        M4.set_speed(q_speed, Motor.CW)
        M1.start_sync(M2, M3, M4)

    elif dir_name == "LINKS":
        M1.set_speed(q_speed, Motor.CCW)
        M2.set_speed(q_speed, Motor.CW)
        M3.set_speed(q_speed, Motor.CW)
        M4.set_speed(q_speed, Motor.CCW)
        M1.start_sync(M2, M3, M4)

    elif dir_name == "RECHTS":
        M1.set_speed(q_speed, Motor.CW)
        M2.set_speed(q_speed, Motor.CCW)
        M3.set_speed(q_speed, Motor.CCW)
        M4.set_speed(q_speed, Motor.CW)
        M1.start_sync(M2, M3, M4)

    elif dir_name == "VORNE-LINKS":
        M2.stop_sync(M3)
        M1.set_speed(q_speed, Motor.CCW)
        M4.set_speed(q_speed, Motor.CCW)
        M1.start_sync(M4)

    elif dir_name == "VORNE-RECHTS":
        M1.stop_sync(M4)
        M2.set_speed(q_speed, Motor.CCW)
        M3.set_speed(q_speed, Motor.CCW)
        M2.start_sync(M3)

    elif dir_name == "HINTEN-LINKS":
        M1.stop_sync(M4)
        M2.set_speed(q_speed, Motor.CW)
        M3.set_speed(q_speed, Motor.CW)
        M2.start_sync(M3)

    elif dir_name == "HINTEN-RECHTS":
        M2.stop_sync(M3)
        M1.set_speed(q_speed, Motor.CW)
        M4.set_speed(q_speed, Motor.CW)
        M1.start_sync(M4)


def apply_360_motion(lx: int, ly: int):
    """
    Stufenlose 360-Grad Vektorsteuerung fuer beliebige Zwischenwinkel:
      Radpaar (M1, M4) = ly - lx
      Radpaar (M2, M3) = ly + lx
    """
    global last_drive_cmd
    mag = min(100.0, math.hypot(lx, ly))
    if mag < DEADZONE_PCT:
        stop_motors()
        return

    raw_14 = float(ly - lx)
    raw_23 = float(ly + lx)
    peak = max(abs(raw_14), abs(raw_23), 1.0)

    target_max = (mag / 100.0) * MAX_SPEED
    s14 = int(round((raw_14 / peak) * target_max / 16.0) * 16)
    s23 = int(round((raw_23 / peak) * target_max / 16.0) * 16)

    if abs(s14) < MIN_SPEED:
        s14 = 0
    if abs(s23) < MIN_SPEED:
        s23 = 0

    if s14 == 0 and s23 == 0:
        stop_motors()
        return

    cmd_key = ("360", s14, s23)
    if cmd_key == last_drive_cmd:
        return
    last_drive_cmd = cmd_key

    # Radpaar M1 & M4 setzen
    if s14 == 0:
        M1.stop_sync(M4)
    else:
        dir_14 = Motor.CCW if s14 > 0 else Motor.CW
        M1.set_speed(min(MAX_SPEED, abs(s14)), dir_14)
        M4.set_speed(min(MAX_SPEED, abs(s14)), dir_14)
        M1.start_sync(M4)

    # Radpaar M2 & M3 setzen
    if s23 == 0:
        M2.stop_sync(M3)
    else:
        dir_23 = Motor.CCW if s23 > 0 else Motor.CW
        M2.set_speed(min(MAX_SPEED, abs(s23)), dir_23)
        M3.set_speed(min(MAX_SPEED, abs(s23)), dir_23)
        M2.start_sync(M3)


def classify_8way_direction(lx: int, ly: int):
    """Bestimmt aus (lx, ly) die 8-Wege-Richtung (inkl. der 4 Diagonalen) und das Tempo."""
    mag = min(100.0, math.hypot(lx, ly))
    if mag < DEADZONE_PCT:
        return "STOP", 0, 0

    # Winkel in Grad: 0° = Vorne (+Y), +90° = Rechts (+X), -90° = Links (-X), +-180° = Hinten (-Y)
    angle = math.degrees(math.atan2(lx, ly))

    if -22.5 <= angle < 22.5:
        dir_name = "VORNE"
    elif 22.5 <= angle < 67.5:
        dir_name = "VORNE-RECHTS"
    elif 67.5 <= angle < 112.5:
        dir_name = "RECHTS"
    elif 112.5 <= angle < 157.5:
        dir_name = "HINTEN-RECHTS"
    elif angle >= 157.5 or angle < -157.5:
        dir_name = "HINTEN"
    elif -157.5 <= angle < -112.5:
        dir_name = "HINTEN-LINKS"
    elif -112.5 <= angle < -67.5:
        dir_name = "LINKS"
    else:
        dir_name = "VORNE-LINKS"

    speed = int(round((mag / 100.0) * MAX_SPEED))
    return dir_name, speed, int(round(mag))


def on_btn_mode_clicked(_event):
    global free_360_mode
    free_360_mode = not free_360_mode
    if has_gui and display is not None:
        display.set_attr("txt_btn_mode.text", "Modus: 360°" if free_360_mode else "Modus: 8-Wege")


def on_btn_exit_clicked(_event):
    stop_motors()
    if has_gui and display is not None:
        try:
            display.stop()
        except Exception:
            pass
    os._exit(0)


if has_gui and display is not None:
    try:
        display.button_clicked("txt_btn_mode", on_btn_mode_clicked)
        display.button_clicked("txt_btn_exit", on_btn_exit_clicked)
    except Exception:
        pass

atexit.register(stop_motors)
signal.signal(signal.SIGTERM, lambda *_a: sys.exit(0))
signal.signal(signal.SIGINT, lambda *_a: sys.exit(0))


def main():
    global free_360_mode, last_remote_channel

    remote = FtBtRemote(i2c_bus=3, i2c_addr=0x42, deadzone=6)
    print("=== Omniwheel 1-Stick Steuerung (Linker Joystick) gestartet ===", flush=True)

    last_log = None
    last_ui_time = 0.0

    while True:
        ok = remote.update()
        now = time.time()

        if not ok:
            stop_motors()
            if has_gui and display is not None and (now - last_ui_time > 0.3):
                display.set_attrs({
                    "txt_info.text": "Warte auf ESP32 (I2C 0x42)...",
                    "txt_dir.text": "STOP (I2C getrennt)",
                    "joy_canvas.joyX": 0,
                    "joy_canvas.joyY": 0,
                })
                last_ui_time = now
            time.sleep(0.2)
            continue

        if not remote.connected:
            stop_motors()
            if last_log != "waiting":
                last_log = "waiting"
                print("ESP32 bereit - warte auf Einschalten der blauen Fernsteuerung...", flush=True)
            if has_gui and display is not None and (now - last_ui_time > 0.25):
                display.set_attrs({
                    "txt_info.text": "ESP32 bereit | Sender einschalten",
                    "txt_dir.text": "STOP (0%)",
                    "joy_canvas.joyX": 0,
                    "joy_canvas.joyY": 0,
                })
                last_ui_time = now
            time.sleep(0.1)
            continue

        # SELECT-Taste an der Fernbedienung schaltet ebenfalls zwischen 8-Wege (Kanal 1) und 360° (Kanal 2) um
        if remote.channel != last_remote_channel:
            last_remote_channel = remote.channel
            free_360_mode = (remote.channel == 1)
            if has_gui and display is not None:
                display.set_attr("txt_btn_mode.text", "Modus: 360°" if free_360_mode else "Modus: 8-Wege")

        lx = remote.joy_left_x
        ly = remote.joy_left_y
        dir_name, speed, pct = classify_8way_direction(lx, ly)

        if free_360_mode:
            apply_360_motion(lx, ly)
            mode_str = "360°"
        else:
            apply_8way_motion(dir_name, speed)
            mode_str = "8-Wege"

        log_key = (mode_str, dir_name, lx // 4, ly // 4)
        if log_key != last_log:
            last_log = log_key
            print(
                f"[{mode_str}] Links(X={lx:4d}%, Y={ly:4d}%) -> {dir_name:13s} | Speed={speed:3d}/512 ({pct:3d}%)",
                flush=True,
            )

        if has_gui and display is not None and (now - last_ui_time > 0.08):
            display.set_attrs({
                "txt_info.text": f"BT Verbunden | {mode_str} (Kanal {remote.channel + 1})",
                "txt_dir.text": f"{dir_name} ({pct}% | v={speed})",
                "joy_canvas.joyX": int(lx),
                "joy_canvas.joyY": int(ly),
                "joy_canvas.motionDir": dir_name,
            })
            last_ui_time = now

        time.sleep(0.025)


if __name__ == "__main__":
    main()
