// お守り型歩数計（M5StickS3）の端末プログラム
//
// - 加速度センサーで歩数を数え、10分ごとに区切って本体に記録する（電源が切れても残す）
// - PC（scripts/ble_gateway.py）が BLE で接続してきたら、まだ送っていない記録を送る
// - PC がサーバーに保存できたら ACK が来るので、そこで初めて記録を消す
// - 画面には今日の歩数を出す（ボタンA で表示、ボタンB で端末の情報）
//
// 通信の決まりは docs/ble_protocol.md。設定は config.h。
// 必要なライブラリ: M5Unified（M5GFX も一緒に入る）、NimBLE-Arduino 2.x
// ※ 2026-10-09 時点でコンパイル・実機での確認はまだ（firmware/README.md 参照）

#include <M5Unified.h>
#include <NimBLEDevice.h>
#include <Preferences.h>
#include <math.h>
#include <sys/time.h>
#include <time.h>

#include "config.h"

// ======================================================================== 記録

struct Record {
  uint32_t ts;        // measured_at（UNIX 秒。時計が合う前は「起動してからの秒」）
  uint16_t steps;     // その時間帯の歩数
  uint16_t reserved;  // 使わない（大きさを 8 バイトにそろえるため）
};

static Record records[MAX_RECORDS];
static size_t recordCount = 0;
static uint32_t droppedCount = 0;  // いっぱいで古いものを捨てた件数（起動してから）

static uint32_t slotEnd = 0;     // 今の時間帯の終わり
static uint32_t slotSteps = 0;   // 今の時間帯の歩数
static uint32_t todaySteps = 0;  // 画面に出す今日の歩数
static uint32_t todayDate = 0;   // todaySteps の日付（日本時間 yyyymmdd。0 = わからない）

static Preferences prefs;

// ======================================================================== 時計
// 本体の時刻は UTC で持ち、画面に出すときだけ日本時間（+9時間）にする。

static bool clockValid() { return time(nullptr) >= (time_t)VALID_EPOCH; }

static uint32_t nowSeconds() {
  // 時計が合う前は「起動してからの秒」を使い、合ったあとで本当の時刻に直す
  return clockValid() ? (uint32_t)time(nullptr) : millis() / 1000;
}

static uint32_t slotEndFor(uint32_t t) { return (t / SLOT_SECONDS + 1) * SLOT_SECONDS; }

static uint32_t jstDate(uint32_t unixSeconds) {
  time_t t = (time_t)unixSeconds + JST_OFFSET_SEC;
  struct tm tmv;
  gmtime_r(&t, &tmv);
  return (uint32_t)((tmv.tm_year + 1900) * 10000 + (tmv.tm_mon + 1) * 100 + tmv.tm_mday);
}

static void setClock(uint32_t unixSeconds) {
  struct timeval tv;
  tv.tv_sec = (time_t)unixSeconds;
  tv.tv_usec = 0;
  settimeofday(&tv, nullptr);
}

// RTC（時計の部品）があれば、電源を入れ直しても時刻がわかるようにする
static void loadClockFromRtc() {
  if (!M5.Rtc.isEnabled()) return;
  auto dt = M5.Rtc.getDateTime();
  if (dt.date.year < 2024) return;
  struct tm tmv = {};
  tmv.tm_year = dt.date.year - 1900;
  tmv.tm_mon = dt.date.month - 1;
  tmv.tm_mday = dt.date.date;
  tmv.tm_hour = dt.time.hours;
  tmv.tm_min = dt.time.minutes;
  tmv.tm_sec = dt.time.seconds;
  time_t t = mktime(&tmv);  // TZ を UTC にしてあるので UTC として読む
  if (t >= (time_t)VALID_EPOCH) setClock((uint32_t)t);
}

static void saveClockToRtc(uint32_t unixSeconds) {
  if (!M5.Rtc.isEnabled()) return;
  time_t t = (time_t)unixSeconds;
  struct tm tmv;
  gmtime_r(&t, &tmv);
  M5.Rtc.setDateTime(&tmv);
}

// ======================================================================== 保存（電源が切れても残す）

static void saveState() {
  if (recordCount > 0) {
    prefs.putBytes("recs", records, recordCount * sizeof(Record));
  } else {
    prefs.remove("recs");  // 0 バイトは書けないので、消して「空」にする
  }
  prefs.putUInt("today", todaySteps);
  prefs.putUInt("tdate", todayDate);
}

static void loadState() {
  size_t len = prefs.getBytesLength("recs");
  if (len > 0 && len % sizeof(Record) == 0 && len <= sizeof(records)) {
    prefs.getBytes("recs", records, len);
    recordCount = len / sizeof(Record);
  }
  // 前に起動したときの「時計が合う前」の記録は、本当の時刻に直せないので捨てる
  size_t keep = 0;
  for (size_t i = 0; i < recordCount; i++) {
    if (records[i].ts >= VALID_EPOCH) records[keep++] = records[i];
  }
  if (keep != recordCount) {
    Serial.printf("[load] 時刻のわからない記録を %u 件捨てました\n", (unsigned)(recordCount - keep));
    recordCount = keep;
  }
  todaySteps = prefs.getUInt("today", 0);
  todayDate = prefs.getUInt("tdate", 0);
  Serial.printf("[load] 未送信 %u 件、今日の歩数 %lu\n", (unsigned)recordCount, (unsigned long)todaySteps);
}

static void appendRecord(uint32_t ts, uint32_t steps) {
  if (recordCount > 0) {
    uint32_t last = records[recordCount - 1].ts;
    bool sameKind = (ts >= VALID_EPOCH) == (last >= VALID_EPOCH);
    if (sameKind && ts <= last) ts = last + 1;  // measured_at が重なるとサーバーで重複扱いになるのでずらす
  }
  if (recordCount >= MAX_RECORDS) {
    memmove(&records[0], &records[1], (MAX_RECORDS - 1) * sizeof(Record));
    recordCount = MAX_RECORDS - 1;
    droppedCount++;
  }
  records[recordCount].ts = ts;
  records[recordCount].steps = steps > 0xFFFF ? 0xFFFF : (uint16_t)steps;
  records[recordCount].reserved = 0;
  recordCount++;
}

// 今の時間帯を区切って記録にする（歩いていなければ記録しない）
static void closeSlot(uint32_t ts) {
  if (slotSteps == 0) return;
  appendRecord(ts, slotSteps);
  Serial.printf("[slot] %lu 歩を記録（未送信 %u 件）\n", (unsigned long)slotSteps, (unsigned)recordCount);
  slotSteps = 0;
  saveState();
}

static void updateSlotAndDay() {
  uint32_t t = nowSeconds();
  if (t >= slotEnd) {
    closeSlot(slotEnd);
    slotEnd = slotEndFor(t);
  }
  if (clockValid()) {
    uint32_t d = jstDate(t);
    if (d != todayDate) {
      if (todayDate != 0) todaySteps = 0;  // 日付が変わった（日付がわからなかったときは、それまでの分を今日に入れる）
      todayDate = d;
      saveState();
    }
  }
}

// ======================================================================== 歩数の検出
// 加速度の大きさから重力の分を引き、揺れの「山」を数える。
// 1歩の中の細かい揺れや、たまたまの揺れを数えないよう、一定のリズムで CONFIRM_STEPS 歩続いてから数え始める。

static float gravityG = 1.0f;
static float swingG = 0.0f;
static bool aboveHigh = false;
static uint32_t lastPeakMs = 0;
static uint8_t candidateSteps = 0;
static bool walking = false;

static void addSteps(uint32_t n) {
  slotSteps += n;
  todaySteps += n;
}

static void onPeak(uint32_t ms) {
  if (lastPeakMs != 0 && ms - lastPeakMs < MIN_STEP_MS) return;  // 同じ1歩の中の揺れ
  bool rhythm = lastPeakMs != 0 && ms - lastPeakMs <= MAX_STEP_MS;
  lastPeakMs = ms;
  if (!rhythm) {
    walking = false;
    candidateSteps = 1;
    return;
  }
  if (walking) {
    addSteps(1);
    return;
  }
  candidateSteps++;
  if (candidateSteps >= CONFIRM_STEPS) {
    addSteps(candidateSteps);
    candidateSteps = 0;
    walking = true;
  }
}

static void sampleImu(uint32_t ms) {
  if (!M5.Imu.update()) return;
  auto data = M5.Imu.getImuData();
  float mag = sqrtf(data.accel.x * data.accel.x + data.accel.y * data.accel.y + data.accel.z * data.accel.z);
  gravityG += (mag - gravityG) * 0.02f;         // ゆっくり追いかける平均＝重力の分
  swingG += ((mag - gravityG) - swingG) * 0.4f;  // 揺れを少しなめらかにする
  if (!aboveHigh && swingG > STEP_HIGH_G) {
    aboveHigh = true;
    onPeak(ms);
  } else if (aboveHigh && swingG < STEP_LOW_G) {
    aboveHigh = false;
  }
}

// ======================================================================== BLE

struct Command {
  uint8_t len;
  uint8_t data[8];
};

static QueueHandle_t commandQueue = nullptr;
static NimBLECharacteristic* dataChar = nullptr;
static volatile bool bleConnected = false;
static volatile uint16_t peerMtu = 23;
static uint32_t sentUntil = 0;  // この接続で送った最後の measured_at（ACK はここまでしか受けつけない）

static uint32_t readU32(const uint8_t* p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void writeRecord(uint8_t* p, const Record& r) {
  p[0] = r.ts & 0xFF;
  p[1] = (r.ts >> 8) & 0xFF;
  p[2] = (r.ts >> 16) & 0xFF;
  p[3] = (r.ts >> 24) & 0xFF;
  p[4] = r.steps & 0xFF;
  p[5] = (r.steps >> 8) & 0xFF;
}

class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* server, NimBLEConnInfo& connInfo) override {
    peerMtu = connInfo.getMTU();
    bleConnected = true;
  }
  void onDisconnect(NimBLEServer* server, NimBLEConnInfo& connInfo, int reason) override {
    bleConnected = false;
    NimBLEDevice::startAdvertising();  // 次の同期のために、また名乗り始める
  }
  void onMTUChange(uint16_t mtu, NimBLEConnInfo& connInfo) override { peerMtu = mtu; }
};

// 書き込まれた命令は BLE の処理の中では実行せず、loop() で順に実行する（記録を同時にさわらないため）
class CtrlCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* characteristic, NimBLEConnInfo& connInfo) override {
    NimBLEAttValue value = characteristic->getValue();
    Command cmd = {};
    size_t len = value.length();
    if (len > sizeof(cmd.data)) len = sizeof(cmd.data);
    cmd.len = (uint8_t)len;
    memcpy(cmd.data, value.data(), len);
    xQueueSend(commandQueue, &cmd, 0);
  }
};

static void startBle() {
  NimBLEDevice::init(DEVICE_ID);
  NimBLEDevice::setMTU(247);

  NimBLEServer* server = NimBLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());
  NimBLEService* service = server->createService(SERVICE_UUID);
  dataChar = service->createCharacteristic(DATA_CHAR_UUID, NIMBLE_PROPERTY::NOTIFY);
  NimBLECharacteristic* ctrlChar = service->createCharacteristic(CTRL_CHAR_UUID, NIMBLE_PROPERTY::WRITE);
  ctrlChar->setCallbacks(new CtrlCallbacks());
  // NimBLE 2.x では、サービスはサーバーが動き出すときに自動で始まる（service->start() はいらない）

  // 名前（端末ID）はアドバタイズに、サービス UUID はスキャン応答に入れる（31バイトに収めるため）
  NimBLEAdvertisementData advData;
  advData.setFlags(BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP);
  advData.setName(DEVICE_ID);
  NimBLEAdvertisementData scanData;
  scanData.setCompleteServices(NimBLEUUID(SERVICE_UUID));

  NimBLEAdvertising* advertising = NimBLEDevice::getAdvertising();
  advertising->enableScanResponse(true);
  advertising->setAdvertisementData(advData);
  advertising->setScanResponseData(scanData);
  advertising->start();
  Serial.printf("[ble] %s として名乗り始めました\n", DEVICE_ID);
}

static bool notifyPacket(const uint8_t* data, size_t len) {
  dataChar->setValue(data, len);
  for (int attempt = 0; attempt < 5; attempt++) {
    if (dataChar->notify()) {
      delay(10);  // 送りすぎてあふれないよう少し待つ
      return true;
    }
    delay(30);
  }
  Serial.println("[ble] 通知を送れませんでした");
  return false;
}

static void applyTimeSync(uint32_t unixNow) {
  bool wasValid = clockValid();
  uint32_t before = nowSeconds();
  setClock(unixNow);
  saveClockToRtc(unixNow);
  if (!wasValid) {
    // 時計が合う前の記録（起動してからの秒）を本当の時刻に直す
    int64_t offset = (int64_t)unixNow - (int64_t)before;
    for (size_t i = 0; i < recordCount; i++) {
      if (records[i].ts < VALID_EPOCH) records[i].ts = (uint32_t)((int64_t)records[i].ts + offset);
    }
    slotEnd = slotEndFor(unixNow);
  } else {
    int64_t diff = (int64_t)unixNow - (int64_t)before;
    if (diff > (int64_t)SLOT_SECONDS || diff < -(int64_t)SLOT_SECONDS) slotEnd = slotEndFor(unixNow);
  }
  updateSlotAndDay();
  saveState();
  Serial.printf("[cmd] 時計を合わせました（%lu）\n", (unsigned long)unixNow);
}

static void sendRecords() {
  // 今の時間帯の途中までの歩数も、ここで区切って送る（管理者用サイトに早く出るように）
  if (clockValid()) closeSlot(nowSeconds());

  size_t perPacket = (peerMtu > 4) ? (size_t)(peerMtu - 3 - 1) / 6 : 1;
  if (perPacket < 1) perPacket = 1;
  if (perPacket > MAX_PER_PACKET) perPacket = MAX_PER_PACKET;

  uint8_t buf[1 + MAX_PER_PACKET * 6];
  size_t n = 0;
  size_t sent = 0;
  uint32_t lastSent = 0;
  for (size_t i = 0; i < recordCount; i++) {
    if (records[i].ts < VALID_EPOCH) continue;  // 時刻のわからない記録は送らない
    writeRecord(&buf[1 + n * 6], records[i]);
    lastSent = records[i].ts;
    n++;
    if (n == perPacket) {
      buf[0] = (uint8_t)n;
      if (!notifyPacket(buf, 1 + n * 6)) return;
      sent += n;
      n = 0;
    }
  }
  if (n > 0) {
    buf[0] = (uint8_t)n;
    if (!notifyPacket(buf, 1 + n * 6)) return;
    sent += n;
  }
  buf[0] = 0;  // 送り終わりの合図
  notifyPacket(buf, 1);
  sentUntil = lastSent;
  Serial.printf("[cmd] %u 件送りました（MTU %u）\n", (unsigned)sent, (unsigned)peerMtu);
}

static void applyAck(uint32_t upTo) {
  if (upTo > sentUntil) upTo = sentUntil;  // この接続で送った分までしか消さない
  size_t keep = 0;
  for (size_t i = 0; i < recordCount; i++) {
    bool saved = records[i].ts >= VALID_EPOCH && records[i].ts <= upTo;
    if (!saved) records[keep++] = records[i];
  }
  unsigned removed = (unsigned)(recordCount - keep);
  recordCount = keep;
  saveState();
  Serial.printf("[cmd] ACK: %u 件消しました（未送信 %u 件）\n", removed, (unsigned)recordCount);
}

static void handleCommands() {
  Command cmd;
  while (xQueueReceive(commandQueue, &cmd, 0) == pdTRUE) {
    if (cmd.len >= 5 && cmd.data[0] == CMD_TIME_SYNC) {
      applyTimeSync(readU32(&cmd.data[1]));
    } else if (cmd.len >= 1 && cmd.data[0] == CMD_SEND) {
      sendRecords();
    } else if (cmd.len >= 5 && cmd.data[0] == CMD_ACK) {
      applyAck(readU32(&cmd.data[1]));
    } else {
      Serial.printf("[cmd] 知らない命令です（%u バイト）\n", (unsigned)cmd.len);
    }
  }
}

// ======================================================================== 画面

static M5Canvas canvas(&M5.Display);
static bool screenOn = true;
static bool infoMode = false;
static uint32_t screenOnSince = 0;
static uint32_t lastDrawMs = 0;

static void setScreen(bool on) {
  if (on == screenOn) return;
  screenOn = on;
  if (on) {
    M5.Display.wakeup();
    M5.Display.setBrightness(SCREEN_BRIGHTNESS);
  } else {
    M5.Display.setBrightness(0);
    M5.Display.sleep();
  }
}

static String clockText() {
  if (!clockValid()) return String("時計まだ");
  time_t t = time(nullptr) + JST_OFFSET_SEC;
  struct tm tmv;
  gmtime_r(&t, &tmv);
  char buf[8];
  snprintf(buf, sizeof(buf), "%02d:%02d", tmv.tm_hour, tmv.tm_min);
  return String(buf);
}

static void drawMain(int w, int h) {
  canvas.setTextColor(TFT_LIGHTGREY);
  canvas.setFont(&fonts::lgfxJapanGothic_20);
  canvas.setTextDatum(top_left);
  canvas.drawString("きょうの歩数", 8, 6);

  canvas.setTextColor(TFT_WHITE);
  canvas.setFont(&fonts::Font7);
  canvas.setTextDatum(middle_right);
  canvas.drawString(String((unsigned long)todaySteps), w - 34, h / 2 + 4);
  canvas.setFont(&fonts::lgfxJapanGothic_20);
  canvas.setTextDatum(bottom_right);
  canvas.drawString("歩", w - 8, h / 2 + 26);

  canvas.setFont(&fonts::lgfxJapanGothic_16);
  canvas.setTextColor(TFT_LIGHTGREY);
  canvas.setTextDatum(bottom_left);
  canvas.drawString(clockText(), 8, h - 4);
  if (bleConnected) {
    canvas.setTextColor(TFT_CYAN);
    canvas.setTextDatum(bottom_center);
    canvas.drawString("通信中", w / 2, h - 4);
  }
  int32_t battery = M5.Power.getBatteryLevel();
  canvas.setTextColor(battery >= 0 && battery < 20 ? TFT_ORANGE : TFT_LIGHTGREY);
  canvas.setTextDatum(bottom_right);
  canvas.drawString(battery >= 0 ? String((long)battery) + "%" : String("--%"), w - 8, h - 4);
}

static void drawInfo(int w, int h) {
  canvas.setFont(&fonts::lgfxJapanGothic_16);
  canvas.setTextDatum(top_left);
  canvas.setTextColor(TFT_WHITE);
  int y = 6;
  const int lineH = 21;
  canvas.drawString(String("端末ID: ") + DEVICE_ID, 8, y);
  y += lineH;
  canvas.drawString(String("未送信: ") + String((unsigned long)recordCount) + "件", 8, y);
  y += lineH;
  canvas.drawString(String("時計: ") + (clockValid() ? "合っている" : "まだ合っていない"), 8, y);
  y += lineH;
  canvas.drawString(String("通信: ") + (bleConnected ? "接続中" : "待っている"), 8, y);
  y += lineH;
  if (droppedCount > 0) {
    canvas.setTextColor(TFT_ORANGE);
    canvas.drawString(String("捨てた記録: ") + String((unsigned long)droppedCount) + "件", 8, y);
  } else if (!M5.Imu.isEnabled()) {
    canvas.setTextColor(TFT_ORANGE);
    canvas.drawString("加速度センサーが見つかりません", 8, y);
  }
}

static void drawScreen() {
  int w = canvas.width();
  int h = canvas.height();
  canvas.fillScreen(TFT_BLACK);
  if (infoMode) {
    drawInfo(w, h);
  } else {
    drawMain(w, h);
  }
  canvas.pushSprite(0, 0);
}

static void handleButtonsAndScreen(uint32_t ms) {
  if (M5.BtnA.wasPressed()) {  // 画面をつける（今日の歩数）
    infoMode = false;
    setScreen(true);
    screenOnSince = ms;
    lastDrawMs = 0;
  }
  if (M5.BtnB.wasPressed()) {  // 端末の情報
    infoMode = true;
    setScreen(true);
    screenOnSince = ms;
    lastDrawMs = 0;
  }
  if (screenOn && ms - screenOnSince > SCREEN_ON_MS) setScreen(false);
  if (screenOn && (lastDrawMs == 0 || ms - lastDrawMs >= SCREEN_REDRAW_MS)) {
    drawScreen();
    lastDrawMs = ms;
  }
}

// ======================================================================== setup / loop

static uint32_t lastSampleMs = 0;
static bool wasConnected = false;

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  setCpuFrequencyMhz(80);  // 電池を長持ちさせる（BLE には 80MHz 以上が必要）
  setenv("TZ", "UTC0", 1);  // 本体の時刻は UTC で持つ
  tzset();

  M5.Display.setRotation(1);
  M5.Display.setBrightness(SCREEN_BRIGHTNESS);
  canvas.setColorDepth(16);
  canvas.createSprite(M5.Display.width(), M5.Display.height());

  commandQueue = xQueueCreate(8, sizeof(Command));
  prefs.begin("omamori", false);
  loadClockFromRtc();
  loadState();
  slotEnd = slotEndFor(nowSeconds());
  if (!M5.Imu.isEnabled()) Serial.println("[imu] 加速度センサーが見つかりません");

  startBle();
  screenOnSince = millis();
}

void loop() {
  M5.update();
  uint32_t ms = millis();

  if (ms - lastSampleMs >= SAMPLE_MS) {
    lastSampleMs = ms;
    sampleImu(ms);
  }

  bool connected = bleConnected;
  if (connected && !wasConnected) sentUntil = 0;  // 新しい接続では、まだ何も送っていない
  wasConnected = connected;

  handleCommands();
  updateSlotAndDay();
  handleButtonsAndScreen(ms);
  delay(2);
}
