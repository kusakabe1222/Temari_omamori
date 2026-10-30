// お守り型歩数計（M5StickS3）の設定。端末ごとに変えるのは DEVICE_ID だけ。
#pragma once
#include <stdint.h>
#include <stddef.h>

// ===== 端末ごとに変える ==================================================
// 管理者用サイトの「端末ID」と同じにする。英数字と _ - : . だけ、64文字まで。
#define DEVICE_ID "omamori-01"

// ===== BLE の決まり（docs/ble_protocol.md・scripts/ble_gateway.py と同じ値にすること） =====
#define SERVICE_UUID   "9a680eec-1bbc-4a41-8d1a-5825119870c1"
#define DATA_CHAR_UUID "49501394-5798-42c9-a6da-da5ca2b387b3"  // 端末 → PC（notify）
#define CTRL_CHAR_UUID "33eeafb4-bd92-45e4-a92d-925ebcc8835d"  // PC → 端末（write）

constexpr uint8_t CMD_TIME_SYNC = 0x01;  // + uint32 今の UNIX 時刻
constexpr uint8_t CMD_SEND      = 0x02;  // まだ ACK されていない記録を送る
constexpr uint8_t CMD_ACK       = 0x03;  // + uint32 この時刻までの記録は消してよい

// ===== 記録 ===============================================================
constexpr uint32_t SLOT_SECONDS   = 600;         // 10分ごとに区切って1件にする
constexpr size_t   MAX_RECORDS    = 432;         // 持っておける件数（歩いた時間帯だけ記録するので、およそ5〜6日分）
constexpr uint32_t VALID_EPOCH    = 1704067200;  // 2024-01-01 00:00 UTC。これより前の時刻は「時計が合っていない」
constexpr size_t   MAX_PER_PACKET = 40;          // 1回の通知に入れる件数の上限（MTU 247 のとき 40件）

// ===== 歩数の検出（step_detector.h。実際に身につけて歩き、数えた歩数と比べて調整する） =====
constexpr uint32_t SAMPLE_MS     = 20;     // 加速度を読む間隔（50回/秒）
constexpr float    GRAVITY_ALPHA = 0.02f;  // 重力の向きを追いかける速さ（小さいほどゆっくり）
// 1歩の候補（上下の揺れの山）
constexpr float    STEP_HIGH_G   = 0.10f;  // 上下の揺れがこれを超えたら「1歩の山」（単位 g）
constexpr float    STEP_LOW_G    = 0.03f;  // 一度これより下がるまで次の山を数えない
constexpr uint32_t MIN_STEP_MS   = 350;    // これより短い間隔の山は同じ1歩の揺れとみなす
constexpr uint32_t MAX_STEP_MS   = 2000;   // これより間があいたら、歩くのをやめたとみなす
// 歩いているかの確かめ（手で振ったのを数えないため）
constexpr uint8_t  CONFIRM_STEPS     = 6;      // 間隔のそろった山がこの回数続いたら、まとめて数え始める
constexpr float    CONFIRM_TOLERANCE = 0.30f;  // たしかめるとき、間隔が平均から ±30% 以内ならそろっている
constexpr float    WALKING_TOLERANCE = 0.50f;  // 歩いている途中で、間隔が平均から ±50% をこえたら、たしかめ直す
constexpr float    SHAKE_ACCEL_G     = 1.2f;   // 重力を除いた揺れがこれをこえたら「振っている」（歩くだけではまず届かない）
constexpr float    SHAKE_GYRO_DPS    = 300.0f; // 回転の速さ（度/秒）がこれをこえたら「振っている」
constexpr uint32_t SHAKE_COOLDOWN_MS = 2000;   // 「振っている」とみなしたあと、数えない時間
constexpr float    SIDEWAYS_RATIO    = 2.0f;   // 横・前後の揺れが上下の揺れのこの倍をこえる1歩は数えない
// 1 にすると、山ごとに数えた／はじいた理由をシリアルモニタに出す（しきい値を調整するとき用）
#define STEP_DEBUG 0

// ===== 画面 ===============================================================
constexpr uint32_t SCREEN_ON_MS      = 15000;  // ボタンを押してから画面が消えるまで
constexpr uint32_t SCREEN_REDRAW_MS  = 500;
constexpr uint8_t  SCREEN_BRIGHTNESS = 96;
constexpr int      JST_OFFSET_SEC    = 9 * 3600;
