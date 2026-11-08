#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ft_bt_remote.py - Treiber & Komplettprogramm fuer den fischertechnik TXT 4.0

Liest die blaue fischertechnik Bluetooth-Fernsteuerung (BT Control Set) ueber
die ESP32-I2C-Bridge am Anschluss EXT1 oder EXT2 des TXT 4.0 aus und steuert
live die Ausgaenge M1..M4 sowie die Servo-Anschluesse S1..S3 an.

Kann sowohl direkt ueber das Touch-Display des TXT 4.0 (unter "File" ->
"bt_remote_control" -> "ft_bt_remote.py") gestartet als auch in eigene
ROBO Pro Coding Projekte importiert werden.
"""

import atexit
import signal
import struct
import sys
import time
from smbus2 import SMBus, i2c_msg


class FtBtRemote:
    """
    Treiberklasse fuer die ESP32 BLE-zu-I2C Bridge am fischertechnik TXT 4.0.
    Standard-Bus am TXT 4.0 fuer EXT1/EXT2 ist I2C-Bus 3, Slave-Adresse 0x42.
    """

    def __init__(self, i2c_bus: int = 3, i2c_addr: int = 0x42, deadzone: int = 6):
        self.i2c_bus = i2c_bus
        self.i2c_addr = i2c_addr
        self.deadzone = deadzone

        self.connected = False
        self.channel = 0
        self.joy_left_y = 0   # M1: -100 bis +100
        self.joy_left_x = 0   # M2: -100 bis +100
        self.joy_right_y = 0  # M3: -100 bis +100
        self.joy_right_x = 0  # M4 / Servo: -100 bis +100

    def _apply_deadzone(self, val: int) -> int:
        if abs(val) < self.deadzone:
            return 0
        # Sender liefert -128 .. +127 -> sauber auf -100 .. +100 Prozent skalieren
        scaled = int(round((val / 127.0) * 100.0))
        return max(-100, min(100, scaled))

    def _read_packet(self):
        msg = i2c_msg.read(self.i2c_addr, 6)
        with SMBus(self.i2c_bus) as bus:
            bus.i2c_rdwr(msg)
        raw = bytes(list(msg))
        return struct.unpack("<BBbbbb", raw)

    def update(self) -> bool:
        """
        Liest die 6 Status-Bytes vom ESP32 ueber I2C aus.
        Gibt True zurueck, wenn die I2C-Kommunikation erfolgreich war.
        """
        try:
            conn, ch, ly, lx, ry, rx = self._read_packet()
            # Falls vorher z.B. i2cdetect ausgefuehrt wurde, einmalig FIFO synchronisieren
            if conn not in (0, 1) or ch not in (0, 1):
                conn, ch, ly, lx, ry, rx = self._read_packet()

            self.connected = bool(conn == 1)
            self.channel = 1 if ch == 1 else 0

            if self.connected:
                self.joy_left_y = self._apply_deadzone(ly)
                self.joy_left_x = self._apply_deadzone(lx)
                self.joy_right_y = self._apply_deadzone(ry)
                self.joy_right_x = self._apply_deadzone(rx)
            else:
                # Failsafe bei getrennter Fernsteuerung
                self.joy_left_y = 0
                self.joy_left_x = 0
                self.joy_right_y = 0
                self.joy_right_x = 0

            return True

        except OSError:
            # Failsafe bei I2C-Lesefehler (z.B. Kabel geloest)
            self.connected = False
            self.joy_left_y = 0
            self.joy_left_x = 0
            self.joy_right_y = 0
            self.joy_right_x = 0
            return False

    @staticmethod
    def to_motor_speed(axis_value: int, max_speed: int = 512) -> int:
        """
        Rechnet einen Joystick-Wert (-100 .. +100) in die TXT 4.0
        Motorgeschwindigkeit (-512 .. +512) um.
        """
        return int(round((axis_value / 100.0) * max_speed))

    @staticmethod
    def to_servo_position(axis_value: int, center: int = 256, span: int = 200) -> int:
        """
        Rechnet einen Joystick-Wert (-100 .. +100) in eine TXT 4.0
        Servoposition (0 .. 512, Mitte = 256) um.
        """
        pos = center + int(round((axis_value / 100.0) * span))
        return max(0, min(512, pos))


# ============================================================================
# Hauptprogramm fuer den TXT 4.0 (direkt ueber das Display oder SSH startbar)
# ============================================================================
def main():
    has_ft_hw = False
    motors = []
    servos = []

    try:
        import ftlock
        import ft
        if ftlock.try_lock(sys.argv[0]) < 0:
            state = ftlock.state()
            print("Hinweis: Ressourcen belegt durch " + str(state.name))
        txt2 = ft.fttxt2()
        motors = [txt2.motor(1), txt2.motor(2), txt2.motor(3), txt2.motor(4)]
        servos = [txt2.servo(1), txt2.servo(2), txt2.servo(3)]
        txt2.update_config()
        ftlock.notify('R')
        has_ft_hw = True
    except Exception as exc:
        print(f"Starte im Diagnose-Modus ohne direkte Motor-Ausgabe ({exc})")

    def stop_all_outputs(*_args):
        if has_ft_hw:
            for m in motors:
                try:
                    m.start_speed(0)
                    m.stop()
                except Exception:
                    pass
            for s in servos:
                try:
                    s.pwm_set(256)
                except Exception:
                    pass

    atexit.register(stop_all_outputs)
    signal.signal(signal.SIGTERM, lambda *_a: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_a: sys.exit(0))

    remote = FtBtRemote(i2c_bus=3, i2c_addr=0x42)
    print("Starte Abfrage der blauen Bluetooth-Fernsteuerung ueber EXT (I2C 0x42)...")

    last_state = None
    while True:
        ok = remote.update()
        if not ok:
            stop_all_outputs()
            print("Warte auf ESP32 an EXT1/EXT2 (I2C-Adresse 0x42)...")
            time.sleep(0.5)
            continue

        if remote.connected:
            m1_speed = remote.to_motor_speed(remote.joy_left_y)
            m2_speed = remote.to_motor_speed(remote.joy_left_x)
            m3_speed = remote.to_motor_speed(remote.joy_right_y)
            m4_speed = remote.to_motor_speed(remote.joy_right_x)
            servo_pos = remote.to_servo_position(remote.joy_right_x)

            if has_ft_hw:
                motors[0].start_speed(m1_speed)
                motors[1].start_speed(m2_speed)
                motors[2].start_speed(m3_speed)
                motors[3].start_speed(m4_speed)
                for s in servos:
                    s.pwm_set(servo_pos)

            cur_state = (remote.channel, remote.joy_left_x, remote.joy_left_y, remote.joy_right_x, remote.joy_right_y)
            if cur_state != last_state:
                last_state = cur_state
                print(
                    f"Verbunden (Kanal {remote.channel + 1}) | "
                    f"Links(X={remote.joy_left_x:4d}%, Y={remote.joy_left_y:4d}%) -> M1={m1_speed:4d}, M2={m2_speed:4d} | "
                    f"Rechts(X={remote.joy_right_x:4d}%, Y={remote.joy_right_y:4d}%) -> M3={m3_speed:4d}, M4={m4_speed:4d}, S={servo_pos:3d}",
                    flush=True,
                )
        else:
            stop_all_outputs()
            if last_state != "disconnected":
                last_state = "disconnected"
                print("ESP32 bereit - warte auf Einschalten der blauen Fernsteuerung...", flush=True)
            time.sleep(0.1)

        time.sleep(0.025)  # 40 Hz Abfragerate fuer verzögerungsfreie Steuerung


if __name__ == "__main__":
    main()
