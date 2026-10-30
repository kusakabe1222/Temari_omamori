// 歩数の検出。M5（Arduino）に頼らない部分だけをまとめてある（しきい値は config.h）。
//
// 考え方:
//   歩くと、体は重力の向き（上下）に一定のリズムで揺れる。手で振ったときは、
//   ・揺れが強すぎる  ・本体がくるくる回る  ・横や前後に揺れる  ・リズムがそろわない
//   ことが多いので、それをはじく。
//
//   1. 加速度から重力の向きを求め、「上下の揺れ」の山を1歩の候補にする
//   2. 揺れが強すぎる（SHAKE_ACCEL_G 以上）か、回転が速すぎる（SHAKE_GYRO_DPS 以上）ときは
//      「振っている」とみなし、SHAKE_COOLDOWN_MS のあいだ数えない（それまでの候補も捨てる）
//   3. 1歩のあいだの揺れが、上下より横・前後の方がずっと大きい（SIDEWAYS_RATIO 倍以上）ときは数えない
//   4. 間隔のそろった山が CONFIRM_STEPS 回続いたら「歩いている」として、まとめて数える
//      歩いている途中でリズムが大きくくずれたら、また CONFIRM_STEPS 回たしかめ直す
#pragma once
#include <math.h>
#include <stdint.h>

#include "config.h"

class StepDetector {
 public:
  // はじいた理由（画面やシリアルに出す）
  enum Reason : uint8_t { COUNTED = 0, SHAKE, SIDEWAYS, RHYTHM };

  struct Stats {
    uint32_t counted = 0;        // 数えた歩数
    uint32_t shakePeaks = 0;     // 「振っている」あいだの山（強すぎる・回りすぎる）
    uint32_t sidewaysPeaks = 0;  // 横・前後の揺れが大きすぎた山
    uint32_t rhythmBreaks = 0;   // リズムがそろわなかった回数
    uint32_t rejected() const { return shakePeaks + sidewaysPeaks + rhythmBreaks; }
  };

  // 最後に見た山のようす（調整のとき、シリアルに出して確かめる用）
  struct PeakInfo {
    uint32_t ms = 0;
    float maxAccelG = 0;   // その1歩のあいだの、重力を除いた揺れの最大
    float maxGyroDps = 0;  // その1歩のあいだの、回転の速さの最大
    float sidewaysRatio = 0;  // 横・前後の揺れ ÷ 上下の揺れ
    uint32_t intervalMs = 0;
    Reason reason = COUNTED;
    bool decided = false;  // 数えた／はじいたが決まった山か（たしかめ中の山は false）
  };

  // 加速度（g）と角速度（度/秒）を1回分入れる。新しく数えた歩数を返す（ふつう 0 か 1。
  // 歩きはじめが確かめられたときは、それまでの分をまとめて返す）
  uint32_t update(uint32_t ms, float ax, float ay, float az, float gx, float gy, float gz) {
    if (!started_) {
      grx_ = ax; gry_ = ay; grz_ = az;
      started_ = true;
    }
    // ゆっくり追いかける平均 = 重力の向き
    grx_ += (ax - grx_) * GRAVITY_ALPHA;
    gry_ += (ay - gry_) * GRAVITY_ALPHA;
    grz_ += (az - grz_) * GRAVITY_ALPHA;
    float gnorm = sqrtf(grx_ * grx_ + gry_ * gry_ + grz_ * grz_);
    if (gnorm < 0.3f) return 0;  // 落としている途中など。重力の向きがわからない

    // 重力を除いた揺れを、上下（重力の向き）と、それ以外（横・前後）に分ける
    float dx = ax - grx_, dy = ay - gry_, dz = az - grz_;
    float vertical = (dx * grx_ + dy * gry_ + dz * grz_) / gnorm;
    float dynamic = sqrtf(dx * dx + dy * dy + dz * dz);
    float sideways2 = dynamic * dynamic - vertical * vertical;
    float gyro = sqrtf(gx * gx + gy * gy + gz * gz);

    // 強すぎる揺れ・速すぎる回転 = 手で振っている。しばらく数えない
    if (dynamic > SHAKE_ACCEL_G || gyro > SHAKE_GYRO_DPS) {
      shakeUntilMs_ = ms + SHAKE_COOLDOWN_MS;
      resetRhythm();
    }

    // 1歩のあいだのようすを集める
    if (dynamic > winMaxAccel_) winMaxAccel_ = dynamic;
    if (gyro > winMaxGyro_) winMaxGyro_ = gyro;
    winVertical2_ += vertical * vertical;
    winSideways2_ += sideways2 > 0 ? sideways2 : 0;

    // 上下の揺れの山をさがす
    vSmooth_ += (vertical - vSmooth_) * 0.4f;
    if (!above_ && vSmooth_ > STEP_HIGH_G) {
      above_ = true;
      return onPeak(ms);
    }
    if (above_ && vSmooth_ < STEP_LOW_G) above_ = false;
    return 0;
  }

  const Stats& stats() const { return stats_; }
  const PeakInfo& lastPeak() const { return last_; }

 private:
  uint32_t onPeak(uint32_t ms) {
    if (lastPeakMs_ != 0 && ms - lastPeakMs_ < MIN_STEP_MS) return 0;  // 同じ1歩の中の小さな揺れ

    uint32_t interval = lastPeakMs_ ? ms - lastPeakMs_ : 0;
    last_ = PeakInfo();
    last_.ms = ms;
    last_.intervalMs = interval;
    last_.maxAccelG = winMaxAccel_;
    last_.maxGyroDps = winMaxGyro_;
    last_.sidewaysRatio = winVertical2_ > 0 ? sqrtf(winSideways2_ / winVertical2_) : 99.0f;
    winMaxAccel_ = winMaxGyro_ = winVertical2_ = winSideways2_ = 0;
    lastPeakMs_ = ms;

    if ((int32_t)(shakeUntilMs_ - ms) > 0) {  // 振っているあいだ
      stats_.shakePeaks++;
      return decide(SHAKE);
    }
    if (last_.sidewaysRatio > SIDEWAYS_RATIO) {
      stats_.sidewaysPeaks++;
      resetRhythm();
      return decide(SIDEWAYS);
    }
    if (interval == 0 || interval > MAX_STEP_MS) {  // 歩きはじめ（または、しばらく止まっていた）
      resetRhythm();
      candidates_ = 1;
      return 0;
    }

    if (walking_) {
      // 歩いているあいだも、リズムが大きくくずれたら、たしかめ直す
      float diff = fabsf((float)interval - avgInterval_);
      if (diff > avgInterval_ * WALKING_TOLERANCE) {
        stats_.rhythmBreaks++;
        resetRhythm();
        candidates_ = 1;
        return decide(RHYTHM);
      }
      avgInterval_ += ((float)interval - avgInterval_) * 0.2f;
      stats_.counted++;
      decide(COUNTED);
      return 1;
    }

    // たしかめ中: 間隔をためる
    if (candidates_ >= 1 && candidates_ - 1 < kMaxIntervals) intervals_[candidates_ - 1] = interval;
    candidates_++;
    if (candidates_ < CONFIRM_STEPS) return 0;

    // CONFIRM_STEPS 回の山の間隔が、平均から CONFIRM_TOLERANCE 以内にそろっているか
    uint8_t n = candidates_ - 1;
    float mean = 0;
    for (uint8_t i = 0; i < n; i++) mean += intervals_[i];
    mean /= n;
    for (uint8_t i = 0; i < n; i++) {
      if (fabsf(intervals_[i] - mean) > mean * CONFIRM_TOLERANCE) {
        // そろっていない: いちばん古い間隔を捨てて、次の山でもう一度たしかめる
        stats_.rhythmBreaks++;
        for (uint8_t j = 1; j < n; j++) intervals_[j - 1] = intervals_[j];
        candidates_--;
        return decide(RHYTHM);
      }
    }
    uint32_t steps = candidates_;  // 歩きはじめの山もふくめて、まとめて数える
    walking_ = true;
    avgInterval_ = mean;
    candidates_ = 0;
    stats_.counted += steps;
    decide(COUNTED);
    return steps;
  }

  uint32_t decide(Reason reason) {
    last_.reason = reason;
    last_.decided = true;
    return 0;
  }

  void resetRhythm() {
    walking_ = false;
    candidates_ = 0;
  }

  static constexpr uint8_t kMaxIntervals = 16;
  static_assert(CONFIRM_STEPS >= 3 && CONFIRM_STEPS - 1 <= kMaxIntervals, "CONFIRM_STEPS は 3〜17 にする");

  bool started_ = false;
  float grx_ = 0, gry_ = 0, grz_ = 1;
  float vSmooth_ = 0;
  bool above_ = false;
  uint32_t lastPeakMs_ = 0;
  uint32_t shakeUntilMs_ = 0;
  float winMaxAccel_ = 0, winMaxGyro_ = 0, winVertical2_ = 0, winSideways2_ = 0;
  bool walking_ = false;
  float avgInterval_ = 0;
  uint8_t candidates_ = 0;
  float intervals_[kMaxIntervals] = {};
  Stats stats_;
  PeakInfo last_;
};
