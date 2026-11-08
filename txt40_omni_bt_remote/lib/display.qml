import QtQuick 2.2
import QtQuick.Window 2.0
import QtQuick.Controls 1.1
import QtQuick.Controls.Styles 1.1
import QtQuick.Extras 1.4

TXTWindow {
  Rectangle {
    id: bg
    color: "#0a0e14"
    anchors.fill: parent
  }

  // Top Status Bar (y: 4..28)
  Rectangle {
    x: 8; y: 4; width: 224; height: 24; radius: 4
    color: "#121a24"; border.color: "#1c2b3d"
    Text {
      id: txt_info
      anchors.centerIn: parent
      text: "Omni BT-Remote | Verbinde..."
      color: "#00e5ff"
      font.pixelSize: 11
      font.bold: true
    }
  }

  // Direction & Speed Info (y: 31..53)
  Rectangle {
    x: 8; y: 31; width: 224; height: 22; radius: 4
    color: "#0e151f"; border.color: "#192638"
    Text {
      id: txt_dir
      anchors.centerIn: parent
      text: "STOP (0%)"
      color: "#ffcc00"
      font.pixelSize: 12
      font.bold: true
    }
  }

  // Interactive Joystick & 8-Way Compass Canvas (y: 56..176, 120x120)
  Canvas {
    id: joy_canvas
    x: 60; y: 56; width: 120; height: 120
    property int joyX: 0
    property int joyY: 0
    property string motionDir: "STOP"

    onJoyXChanged: { requestPaint(); }
    onJoyYChanged: { requestPaint(); }
    onMotionDirChanged: { requestPaint(); }

    onPaint: {
      var ctx = getContext("2d");
      var w = width;
      var h = height;
      var cx = w / 2;
      var cy = h / 2;
      var rMax = w / 2 - 8;

      ctx.fillStyle = "#070b10";
      ctx.fillRect(0, 0, w, h);

      // Outer Circle & Mid Circle
      ctx.strokeStyle = "#1b2a3d";
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.arc(cx, cy, rMax, 0, Math.PI * 2);
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(cx, cy, rMax * 0.5, 0, Math.PI * 2);
      ctx.stroke();

      // 8-Way Axes (Horizontal, Vertical, and 2 Diagonals)
      ctx.strokeStyle = "#162232";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx, cy - rMax); ctx.lineTo(cx, cy + rMax);
      ctx.moveTo(cx - rMax, cy); ctx.lineTo(cx + rMax, cy);
      var d = rMax * 0.7071;
      ctx.moveTo(cx - d, cy - d); ctx.lineTo(cx + d, cy + d);
      ctx.moveTo(cx - d, cy + d); ctx.lineTo(cx + d, cy - d);
      ctx.stroke();

      // Labels V, H, L, R
      ctx.fillStyle = "#8fa5bf";
      ctx.font = "bold 9px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("V", cx, cy - rMax + 9);
      ctx.fillText("H", cx, cy + rMax - 2);
      ctx.fillText("L", cx - rMax + 6, cy + 3);
      ctx.fillText("R", cx + rMax - 6, cy + 3);

      // Draw Active joystick vector
      var px = cx + (joyX / 100.0) * rMax;
      var py = cy - (joyY / 100.0) * rMax;

      if (joyX !== 0 || joyY !== 0) {
        ctx.strokeStyle = "#ffcc00";
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(px, py);
        ctx.stroke();
      }

      // Joystick Knob Dot
      ctx.fillStyle = (joyX === 0 && joyY === 0) ? "#00ff88" : "#ff3366";
      ctx.beginPath();
      ctx.arc(px, py, 5, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // Bottom Control Buttons (y: 182..224)
  TXTButton {
    id: txt_btn_mode
    x: 8; y: 182; width: 110; height: 40
    text: "Modus: 8-Wege"
    font.pixelSize: 12
    enabled: true
  }

  TXTButton {
    id: txt_btn_exit
    x: 124; y: 182; width: 108; height: 40
    text: "Beenden"
    font.pixelSize: 12
    enabled: true
  }
}
