/*
 * esp32_ft_bt_remote_bridge.ino
 *
 * BLE-zu-I2C Bridge fuer die blaue fischertechnik Bluetooth-Fernsteuerung
 * (BT Control Set, Art.-Nr. 540585 / Sender 161942) am fischertechnik TXT 4.0.
 *
 * Funktionsweise:
 * 1. Setzt die Bluetooth-MAC-Adresse des ESP32 auf das LNT-Herstellerpraefix
 *    10:45:F8:xx:xx:xx (zwingend erforderlich, damit sich der blaue Sender
 *    mit dem ESP32 koppelt).
 * 2. Startet über NimBLE einen BLE GATT-Server mit dem Namen "BT Control Receiver"
 *    und legt die GATT-Characteristics exakt auf die vom Handsender erwarteten
 *    Attribute-Handles:
 *      - 0x0018: Battery Level (0x2A19)
 *      - 0x001D: Communication Channel (0 = Kanal 1 blau, 1 = Kanal 2 orange)
 *      - 0x0020: Output Alert
 *      - 0x0024: Output M1 (Linker Joystick Y,  -100 .. +100)
 *      - 0x0026: Output M2 (Linker Joystick X,  -100 .. +100)
 *      - 0x0028: Output M3 (Rechter Joystick Y, -100 .. +100)
 *      - 0x002A: Output M4 (Rechter Joystick X, -100 .. +100)
 * 3. Stellt die empfangenen 4 Joystick-Achsen (-100 bis +100) sowie den
 *    Verbindungs- und Kanalstatus als I2C-Slave (Adresse 0x42) an den Pins
 *    GPIO 21 (SDA) und GPIO 22 (SCL) fuer den EXT1/EXT2-Port des TXT 4.0 bereit.
 * 4. Failsafe: Bei Verbindungsabbruch werden alle Achsen sofort auf 0 gesetzt.
 */

#include <Arduino.h>
#include <Wire.h>
#include <NimBLEDevice.h>

#include "esp_system.h"
#if __has_include("esp_mac.h")
  #include "esp_mac.h"
#endif

// ============================================================================
// I2C-Konfiguration (Verbindung zum TXT 4.0 EXT1 oder EXT2 Anschluss)
// ============================================================================
#define I2C_SLAVE_ADDR  0x42
#define I2C_SDA_PIN     21
#define I2C_SCL_PIN     22
#define I2C_FREQ        100000

// Onboard-LED (bei fast allen ESP32 NodeMCU / DevKitC Boards auf GPIO 2)
#ifndef LED_BUILTIN
  #define LED_BUILTIN   2
#endif

// ============================================================================
// BLE GATT UUIDs des fischertechnik "BT Control Receiver" (Modell 161943)
// ============================================================================
#define DEVICE_NAME             "BT Control Receiver"

// Standard BLE Services
#define UUID_DEVINFO_SERVICE    "180a"
#define UUID_MANU_NAME_CHAR     "2a29"
#define UUID_MODEL_NO_CHAR      "2a24"
#define UUID_HW_REV_CHAR        "2a27"
#define UUID_FW_REV_CHAR        "2a26"
#define UUID_SYS_ID_CHAR        "2a23"

#define UUID_BATT_SERVICE       "180f"
#define UUID_BATT_LEVEL_CHAR    "2a19"

// Proprietaere fischertechnik / LNT BT-Remote Services & Characteristics
#define UUID_COMM_CH_SERVICE    "2e582b3a-c5c5-11e6-9d9d-cec0c932ce01"
#define UUID_COMM_CH_CHAR       "2e582de2-c5c5-11e6-9d9d-cec0c932ce01"

#define UUID_ALERT_SERVICE      "2e582f9a-c5c5-11e6-9d9d-cec0c932ce01"
#define UUID_ALERT_CHAR         "2e58318e-c5c5-11e6-9d9d-cec0c932ce01"

#define UUID_OUTPUT_SERVICE     "2e58327e-c5c5-11e6-9d9d-cec0c932ce01"
#define UUID_OUT_M1_CHAR        "2e583378-c5c5-11e6-9d9d-cec0c932ce01" // Linker Joystick Y  (Handle 0x0024)
#define UUID_OUT_M2_CHAR        "2e58358a-c5c5-11e6-9d9d-cec0c932ce01" // Linker Joystick X  (Handle 0x0026)
#define UUID_OUT_M3_CHAR        "2e583666-c5c5-11e6-9d9d-cec0c932ce01" // Rechter Joystick Y (Handle 0x0028)
#define UUID_OUT_M4_CHAR        "2e5837b0-c5c5-11e6-9d9d-cec0c932ce01" // Rechter Joystick X (Handle 0x002A)

// ============================================================================
// Aktueller Steuerzustand (wird per I2C in 6 Bytes an den TXT 4.0 gesendet)
// ============================================================================
struct RemoteState {
  volatile uint8_t connected;   // Byte 0: 1 = verbunden, 0 = getrennt
  volatile uint8_t channel;     // Byte 1: 0 = Kanal 1 (blau), 1 = Kanal 2 (orange)
  volatile int8_t  joy_left_y;  // Byte 2: M1 / Linker Stick vertikal   (-100 .. +100)
  volatile int8_t  joy_left_x;  // Byte 3: M2 / Linker Stick horizontal (-100 .. +100)
  volatile int8_t  joy_right_y; // Byte 4: M3 / Rechter Stick vertikal  (-100 .. +100)
  volatile int8_t  joy_right_x; // Byte 5: M4 / Rechter Stick horiz.    (-100 .. +100)
};

static RemoteState g_state = {0, 0, 0, 0, 0, 0};
static volatile bool g_updated = false;
static volatile bool g_restart_adv = false;

// ============================================================================
// I2C Slave Callbacks (werden vom TXT 4.0 über SMBus(3) abgefragt)
// ============================================================================
void onI2CRequest() {
  uint8_t packet[6];
  packet[0] = g_state.connected;
  packet[1] = g_state.channel;
  packet[2] = (uint8_t)g_state.joy_left_y;
  packet[3] = (uint8_t)g_state.joy_left_x;
  packet[4] = (uint8_t)g_state.joy_right_y;
  packet[5] = (uint8_t)g_state.joy_right_x;
  Wire.write(packet, sizeof(packet));
}

void onI2CReceive(int numBytes) {
  while (Wire.available()) {
    (void)Wire.read();
  }
}

// ============================================================================
// BLE Server & Characteristic Callbacks
// ============================================================================
class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer *pServer, ble_gap_conn_desc *desc) override {
    g_state.connected = 1;
    g_updated = true;
    digitalWrite(LED_BUILTIN, HIGH);
    Serial.printf("[BLE] Verbunden mit %s\n", NimBLEAddress(desc->peer_ota_addr).toString().c_str());
  }

  void onDisconnect(NimBLEServer *pServer) override {
    // Failsafe: Alle Achsen sofort auf 0 setzen!
    g_state.connected   = 0;
    g_state.joy_left_y  = 0;
    g_state.joy_left_x  = 0;
    g_state.joy_right_y = 0;
    g_state.joy_right_x = 0;
    g_updated           = true;
    g_restart_adv       = true;
    digitalWrite(LED_BUILTIN, LOW);
  }
};

class ChannelWriteCallback : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic *pCharacteristic) override {
    NimBLEAttValue val = pCharacteristic->getValue();
    if (val.length() >= 1) {
      g_state.channel = val.data()[0];
      g_updated = true;
    }
  }
};

class OutputWriteCallback : public NimBLECharacteristicCallbacks {
private:
  uint8_t m_axisIndex;

public:
  explicit OutputWriteCallback(uint8_t axisIndex) : m_axisIndex(axisIndex) {}

  void onWrite(NimBLECharacteristic *pCharacteristic) override {
    NimBLEAttValue raw = pCharacteristic->getValue();
    if (raw.length() >= 1) {
      int8_t val = (int8_t)(raw.data()[0]);
      switch (m_axisIndex) {
        case 0: g_state.joy_left_y  = val; break; // M1 (Handle 0x0024)
        case 1: g_state.joy_left_x  = val; break; // M2 (Handle 0x0026)
        case 2: g_state.joy_right_y = val; break; // M3 (Handle 0x0028)
        case 3: g_state.joy_right_x = val; break; // M4 (Handle 0x002A)
      }
      g_updated = true;
    }
  }
};

void startFTAdvertising() {
  NimBLEAdvertising *pAdvertising = NimBLEDevice::getAdvertising();
  pAdvertising->stop();

  // Exakt das 24-Byte ADV_IND Paket wie in ft_bt_remote_server.c:
  // 02 01 06  14 09 "BT Control Receiver"
  NimBLEAdvertisementData advData;
  advData.setFlags(BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP);
  advData.setName(DEVICE_NAME);
  pAdvertising->setAdvertisementData(advData);

  NimBLEAdvertisementData scanRspData;
  scanRspData.setName(DEVICE_NAME);
  pAdvertising->setScanResponseData(scanRspData);
  pAdvertising->setScanResponse(true);

  // Advertising-Intervall exakt 20ms (0x20 * 0.625ms)
  pAdvertising->setMinInterval(0x20);
  pAdvertising->setMaxInterval(0x20);

  pAdvertising->start();
}

// ============================================================================
// BLE Services einrichten (exakt gleiche Handle-Tabelle wie ft_bt_remote_server.c)
// ============================================================================
void setupBLEServer() {
  // 1. LNT-MAC-Adresse (10:45:F8:xx:xx:xx) setzen, BEVOR BLE initialisiert wird!
  uint8_t baseMac[6] = {0x10, 0x45, 0xF8, 0x11, 0x22, 0x30};
  esp_err_t err = esp_base_mac_addr_set(baseMac);
  if (err != ESP_OK) {
    Serial.printf("WARNUNG: esp_base_mac_addr_set fehlgeschlagen: 0x%X\n", err);
  }

  // 2. NimBLE-Stack initialisieren
  NimBLEDevice::init(DEVICE_NAME);
  NimBLEDevice::setPower(ESP_PWR_LVL_P9, ESP_BLE_PWR_TYPE_DEFAULT);
  NimBLEDevice::setPower(ESP_PWR_LVL_P9, ESP_BLE_PWR_TYPE_ADV);
  NimBLEDevice::setPower(ESP_PWR_LVL_P9, ESP_BLE_PWR_TYPE_SCAN);

  Serial.printf("Aktive BLE MAC-Adresse: %s (muss mit 10:45:f8 beginnen)\n",
                NimBLEDevice::getAddress().toString().c_str());

  NimBLEServer *pServer = NimBLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());

  // In NimBLE belegen GAP (0x1800, Handles 0x0001..0x0007) und
  // GATT (0x1801, Handles 0x0008..0x000B) zusammen 11 Handles (0x0001..0x000B).
  // Damit Battery Service wie im Original exakt bei Handle 0x0016 startet,
  // belegt der Device Info Service genau 10 Handles (0x000C..0x0015):
  //   0x000C: Service 0x180A (1 Handle)
  //   0x000D..0x000E: Manufacturer Name (2 Handles)
  //   0x000F..0x0010: Model Number      (2 Handles)
  //   0x0011..0x0012: Firmware Revision (2 Handles)
  //   0x0013..0x0014: System ID         (2 Handles)
  //   0x0015:         Descriptor 0x2901 (1 Handle)
  NimBLEService *pDevInfo = pServer->createService(UUID_DEVINFO_SERVICE);
  NimBLECharacteristic *pManu = pDevInfo->createCharacteristic(UUID_MANU_NAME_CHAR, NIMBLE_PROPERTY::READ);
  pManu->setValue("fischertechnik");
  NimBLECharacteristic *pModel = pDevInfo->createCharacteristic(UUID_MODEL_NO_CHAR, NIMBLE_PROPERTY::READ);
  pModel->setValue("161943");
  NimBLECharacteristic *pHwRev = pDevInfo->createCharacteristic(UUID_HW_REV_CHAR, NIMBLE_PROPERTY::READ);
  pHwRev->setValue("1");
  NimBLECharacteristic *pFwRev = pDevInfo->createCharacteristic(UUID_FW_REV_CHAR, NIMBLE_PROPERTY::READ);
  uint8_t fwRevBytes[8] = {'1', '.', '4', '8', 0, 0, 0, 0};
  pFwRev->setValue(fwRevBytes, sizeof(fwRevBytes));
  NimBLECharacteristic *pSysId = pDevInfo->createCharacteristic(UUID_SYS_ID_CHAR, NIMBLE_PROPERTY::READ);
  uint8_t sysIdBytes[8] = {0x00, 0x00, 0x00, 0x00, 0x00, 0xF8, 0x45, 0x11};
  pSysId->setValue(sysIdBytes, sizeof(sysIdBytes));
  NimBLEDescriptor *pPadDesc = pSysId->createDescriptor("2901", NIMBLE_PROPERTY::READ);
  pPadDesc->setValue("1");
  pDevInfo->start();

  // 4. Battery Service (0x180F) -> Handles 0x0016 .. 0x001A (5 Handles)
  //    0x0016: Service 0x180F
  //    0x0017: Char Decl 0x2A19
  //    0x0018: Char Value 0x2A19 (Battery Level)
  //    0x0019: Descriptor 0x2902 (auto-erzeugt durch NOTIFY)
  //    0x001A: Descriptor 0x2904 (Presentation Format)
  NimBLEService *pBatt = pServer->createService(UUID_BATT_SERVICE);
  NimBLECharacteristic *pBattLevel = pBatt->createCharacteristic(
      UUID_BATT_LEVEL_CHAR,
      NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY
  );
  uint8_t battLevel = 100;
  pBattLevel->setValue(&battLevel, 1);
  NimBLEDescriptor *pBattFmt = pBattLevel->createDescriptor("2904", NIMBLE_PROPERTY::READ);
  uint8_t battFmtBytes[7] = {0x04, 0x00, 0xAD, 0x27, 0x01, 0x06, 0x01};
  pBattFmt->setValue(battFmtBytes, sizeof(battFmtBytes));
  pBatt->start();

  // 5. Communication Channel Service (2e582b3a-...) -> Handles 0x001B .. 0x001D (3 Handles)
  //    0x001D: Comm Channel Value (0 = Kanal 1 blau, 1 = Kanal 2 orange)
  NimBLEService *pComm = pServer->createService(UUID_COMM_CH_SERVICE);
  NimBLECharacteristic *pCommChar = pComm->createCharacteristic(
      UUID_COMM_CH_CHAR,
      NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR
  );
  uint8_t initChannel = 0;
  pCommChar->setValue(&initChannel, 1);
  pCommChar->setCallbacks(new ChannelWriteCallback());
  pComm->start();

  // 6. Output Alert Service (2e582f9a-...) -> Handles 0x001E .. 0x0021 (4 Handles)
  //    0x0020: Alert Value, 0x0021: CCCD (0x2902)
  NimBLEService *pAlert = pServer->createService(UUID_ALERT_SERVICE);
  NimBLECharacteristic *pAlertChar = pAlert->createCharacteristic(
      UUID_ALERT_CHAR,
      NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY
  );
  uint8_t initAlert = 0;
  pAlertChar->setValue(&initAlert, 1);
  pAlert->start();

  // 7. Output Service (2e58327e-...) -> Handles 0x0022 .. 0x002A (9 Handles)
  //    0x0024 = M1 (Links Y)
  //    0x0026 = M2 (Links X)
  //    0x0028 = M3 (Rechts Y)
  //    0x002A = M4 (Rechts X)
  NimBLEService *pOutput = pServer->createService(UUID_OUTPUT_SERVICE);
  const char *outUUIDs[4] = {UUID_OUT_M1_CHAR, UUID_OUT_M2_CHAR, UUID_OUT_M3_CHAR, UUID_OUT_M4_CHAR};
  NimBLECharacteristic *pOutChars[4];
  for (uint8_t i = 0; i < 4; i++) {
    pOutChars[i] = pOutput->createCharacteristic(
        outUUIDs[i],
        NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR
    );
    pOutChars[i]->setCallbacks(new OutputWriteCallback(i));
  }
  pOutput->start();

  // GATT-Server starten, damit alle Handles vergeben werden
  pServer->start();

  Serial.printf("Handles: Batt=0x%04X (soll 0x0018), CommCh=0x%04X (soll 0x001D), Alert=0x%04X (soll 0x0020)\n",
                pBattLevel->getHandle(), pCommChar->getHandle(), pAlertChar->getHandle());
  for (uint8_t i = 0; i < 4; i++) {
    Serial.printf("Output M%u Handle = 0x%04X (soll 0x%04X)\n",
                  i + 1, pOutChars[i]->getHandle(), 0x0024 + i * 2);
  }

  // 8. Advertising starten
  startFTAdvertising();

  Serial.println("BLE GATT-Server gestartet. Warte auf blaue fischertechnik Fernsteuerung...");
}

// ============================================================================
// Arduino Setup & Loop
// ============================================================================
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== fischertechnik BT Control Receiver Bridge (ESP32 -> TXT 4.0 I2C) ===");

  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);

  // I2C Slave initialisieren (Adresse 0x42 auf SDA=21, SCL=22)
  Wire.onReceive(onI2CReceive);
  Wire.onRequest(onI2CRequest);
  Wire.begin((uint8_t)I2C_SLAVE_ADDR, I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQ);
  Serial.printf("I2C-Slave bereit auf Adresse 0x%02X (SDA=GPIO%d, SCL=GPIO%d)\n",
                I2C_SLAVE_ADDR, I2C_SDA_PIN, I2C_SCL_PIN);

  // BLE Server starten
  setupBLEServer();
}

void loop() {
  if (g_restart_adv) {
    g_restart_adv = false;
    delay(200);
    startFTAdvertising();
    Serial.println("Fernsteuerung getrennt -> Failsafe aktiv (alle Achsen = 0). Suche wieder...");
  }

  if (g_updated) {
    g_updated = false;
    Serial.printf("[BT Remote] Verbunden=%u | Kanal=%u | Links(X=%4d, Y=%4d) | Rechts(X=%4d, Y=%4d)\n",
                  g_state.connected,
                  g_state.channel,
                  g_state.joy_left_x,
                  g_state.joy_left_y,
                  g_state.joy_right_x,
                  g_state.joy_right_y);
  }

  delay(10);
}
