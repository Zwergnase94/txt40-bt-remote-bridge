import os
import sys
import time
import threading
import json
import ftgui

# --- Patch ftgui.fttxt2_gui_connector to eliminate race conditions and screen freezes ---

def _patched_run_queue(self, forced=False):
    with self.setter_lock:
        if self.setter_request is not None:
            if self.setter_request.is_published() or (time.time() - getattr(self, '_last_pub_time', 0) > 0.15):
                self.setter_request = None

        if self.setter_queue and (self.setter_request is None or forced):
            q = self.setter_queue
            self.setter_queue = dict()
            try:
                req = self.client.publish(
                    self.topic_notify,
                    json.dumps({
                        "action": 'UIAttrSet',
                        "pid": str(self.pid),
                        "project": self.project,
                        "payload": q
                    }),
                    qos=1
                )
                self.setter_request = req
                self._last_pub_time = time.time()
            except Exception:
                self.setter_request = None

def _patched_on_publish(self, client, userdata, mid):
    with self.setter_lock:
        if self.setter_request is not None:
            if mid == self.setter_request.mid or self.setter_request.is_published():
                self.setter_request = None
    self.run_queue()

def _patched_set_attrs(self, attr_dict):
    """Set multiple attributes atomically in a single MQTT payload."""
    if not self.is_open():
        return False
    with self.setter_lock:
        for path, value in attr_dict.items():
            ids = str(path).split(".")
            if len(ids) == 2:
                if not self.attributes or (ids[0] in self.attributes and ids[1] in self.attributes[ids[0]]):
                    self.invalidated[str(path)] = True
                    if ids[0] not in self.setter_queue:
                        self.setter_queue[ids[0]] = dict()
                    self.setter_queue[ids[0]][ids[1]] = value
    self.run_queue()
    return True

ftgui.fttxt2_gui_connector.run_queue = _patched_run_queue
ftgui.fttxt2_gui_connector.on_publish = _patched_on_publish
ftgui.fttxt2_gui_connector.set_attrs = _patched_set_attrs

display = ftgui.fttxt2_gui_connector("app")
display.run()

def display_monitoring():
    for _ in range(50):
        if display.running():
            break
        time.sleep(0.1)

    if display.running():
        while display.running():
            time.sleep(0.5)
        os._exit(0)

threading.Thread(target=display_monitoring, daemon=True).start()
