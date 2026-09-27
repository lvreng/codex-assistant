#include "character_motion.h"

#include <algorithm>
#include <cmath>

namespace Character {
namespace {
constexpr float Pi = 3.14159265359f;
float smooth(float x)
{
    x = std::clamp(x, 0.0f, 1.0f);
    return x * x * (3 - 2 * x);
}
float gesture(float t, float start, float attack, float hold, float release)
{
    return smooth((t - start) / attack) *
           (1 - smooth((t - start - attack - hold) / release));
}
float ease_look(float t, float a, float b)
{
    return a + (b - a) * smooth(t / .16f);
}
} // namespace

float Motion::random_unit()
{
    random_ ^= random_ << 13;
    random_ ^= random_ >> 17;
    random_ ^= random_ << 5;
    return (random_ & 0xffffu) / 65535.0f;
}

void Motion::reset(Pet::Theme theme)
{
    *this = Motion{};
    theme_ = theme;
    random_ += static_cast<unsigned>(theme) * 7919u;
}

Pose Motion::target() const
{
    Pose p{};
    const float t = phrase_time_;
    const float breath = std::sin(static_cast<float>(std::fmod(clock_, 24.0)) * Pi / 2.4f);
    const float settle = gesture(t, .5f, .8f, 2.5f, 1.3f);
    p[Stretch] = 1 + breath * .006f;
    p[EyeL] = p[EyeR] = 1;
    p[Pupil] = 1;
    p[MouthWidth] = 44; p[Smile] = 8;
    p[Y] = breath * 1.2f;
    p[BodyY] = -breath * .8f;
    p[HandLX] = 170; p[HandRX] = 310;
    p[HandLY] = 244 + breath; p[HandRY] = 244 - breath;
    p[Shine] = .5f + breath * .5f;
    // Eyes move first and hold a fixation; the slower head spring follows.
    p[GazeX] = t < 2.2f ? 0 : t < 4.8f ? ease_look(t - 2.2f, 0, 7)
                                                      : ease_look(t - 4.8f, 7, 0);
    switch (state_) {
    case Pet::Thinking: {
        p[Focus] = .65f;
        p[Smile] = -2; p[MouthWidth] = 30;
        p[EyeL] = .90f; p[EyeR] = 1.04f;
        p[BrowL] = -7; p[BrowR] = 8; p[BrowLift] = -3;
        if (variant_ == 0) {
            p[GazeX] = 12 * settle; p[GazeY] = -16 * settle;
            p[Roll] = -.045f * settle;
            p[BrowR] += settle * 9; p[BrowLift] -= settle * 5;
            p[MouthX] = -3 * settle;
        } else if (variant_ == 1) {
            p[GazeX] = t < 1 ? ease_look(t - .4f, 0, -17) :
                       t < 3.2f ? -17 : t < 5.5f ? ease_look(t - 3.2f, -17, 15) :
                                                                  ease_look(t - 5.5f, 15, 0);
            p[GazeY] = -6;
            p[Roll] = p[GazeX] * .002f;
            p[EyeL] = .72f; p[BrowL] = -11; p[BrowR] = 13;
        } else if (variant_ == 2) {
            const float chin = gesture(t, .4f, 1, 3.5f, 1.2f);
            p[HandLX] += 56 * chin; p[HandLY] -= 53 * chin;
            p[GazeY] = -10 * chin; p[GazeX] = -9 * chin;
            p[Roll] = -.035f * chin; p[EyeL] = 1 - chin * .32f;
            p[BrowR] = 13 * chin; p[MouthX] = 3 * chin;
            p[Y] += gesture(t, 2.8f, .3f, .1f, .5f) * 2;
        } else if (variant_ == 3) {
            const float insight = gesture(t, 1.6f, .25f, 1.3f, 1.2f);
            p[GazeY] = -11 * settle;
            p[EyeL] = p[EyeR] = .83f + insight * .32f;
            p[BrowLift] = -14 * insight; p[Pupil] = .93f + insight * .11f;
            p[Y] -= 4 * insight; p[Smile] = insight * 10 - 2;
            p[HandRX] += insight * 13; p[HandRY] -= insight * 25;
        } else {
            p[GazeY] = 6 * settle; p[GazeX] = -5 * settle;
            p[EyeL] = 1 - settle * .39f; p[EyeR] = 1 - settle * .2f;
            p[BrowL] = 9 * settle; p[BrowR] = -9 * settle;
            p[Y] += 3 * settle; p[Roll] = .035f * settle;
            p[HandRX] -= settle * 33; p[HandRY] -= settle * 8;
        }
        break;
    }
    case Pet::Done: {
        p[Smile] = 16; p[MouthWidth] = 54;
        p[BrowL] = -5; p[BrowR] = 5; p[BrowLift] = -5;
        const float welcome = state_time_ < 3.5
            ? gesture(static_cast<float>(state_time_), .05f, .32f, 1.0f, 1.5f) : 0;
        p[Celebrate] = welcome;
        p[Happy] = welcome * .9f; p[Jaw] = welcome * 18;
        p[HandLX] -= welcome * 40; p[HandRX] += welcome * 40;
        p[HandLY] -= welcome * 90; p[HandRY] -= welcome * 90;
        p[Y] -= welcome * 6;
        if (variant_ == 0) {
            const float smile = gesture(t, 3.8f, .8f, 1.1f, 1.0f);
            p[Happy] = std::max(p[Happy], smile * .9f);
            p[Smile] += smile * 4; p[Roll] = .025f * settle;
            p[GazeX] = 4 * settle;
        } else if (variant_ == 1) {
            const float nod = gesture(t, 1.2f, .4f, .1f, .5f) +
                              gesture(t, 2.3f, .3f, .1f, .7f);
            p[Y] += nod * 7; p[GazeY] = nod * 4; p[EyeL] = p[EyeR] = 1 - nod * .2f;
            p[BodyY] -= nod * 2; p[Smile] += nod * 4;
        } else if (variant_ == 2) {
            const float wave = gesture(t, .8f, .7f, 2.3f, 1.0f);
            p[HandRX] += wave * (48 + std::sin(t * 7) * 10);
            p[HandRY] -= wave * (83 + std::cos(t * 7) * 4);
            p[Roll] = -.04f * wave; p[GazeX] = 9 * wave;
            p[EyeL] = 1 - gesture(t, 2.2f, .09f, .16f, .24f) * .97f;
            p[Smile] += wave * 5;
        } else if (variant_ == 3) {
            const float ready = gesture(t, 1.0f, .35f, 0, .3f);
            const float hop = gesture(t, 1.45f, .25f, .14f, .6f);
            p[Y] += ready * 4 - hop * 12;
            p[Stretch] += -ready * .04f + hop * .045f;
            p[Happy] = hop * .95f; p[Jaw] = hop * 18;
            p[HandLY] -= hop * 55; p[HandRY] -= hop * 55;
            p[Celebrate] = gesture(t, 1.5f, .2f, .6f, .9f);
        } else {
            const float content = gesture(t, .8f, 1.0f, 1.8f, 1.5f);
            p[Happy] = content * .82f;
            p[Roll] = -.035f * content; p[Stretch] -= .008f * content;
            p[HandLX] += content * 33; p[HandRX] -= content * 33;
            p[HandLY] -= content * 8; p[HandRY] -= content * 8;
        }
        break;
    }
    case Pet::Working: {
        const float key = static_cast<float>(std::fmod(state_time_, 2.1));
        p[Focus] = 1; p[EyeL] = p[EyeR] = .78f;
        p[BrowL] = 16; p[BrowR] = -16;
        p[GazeY] = 8; p[GazeX] = key < 1.4f ? -7 : 10;
        p[Smile] = 6; p[MouthWidth] = 37;
        p[HandLX] = 206; p[HandRX] = 276;
        p[HandLY] = 262 + std::sin(t * 12) * 4;
        p[HandRY] = 262 + std::sin(t * 12 + 2.2f) * 4;
        p[Roll] = -.014f; p[Y] += std::sin(t * 6) * .7f;
        break;
    }
    case Pet::Waiting: {
        p[EyeL] = p[EyeR] = 1.13f; p[Pupil] = .82f;
        p[BrowL] = -10; p[BrowR] = 10; p[BrowLift] = -9;
        p[MouthWidth] = 17; p[Jaw] = 18; p[Smile] = 0;
        p[GazeX] = 0; p[Roll] = .028f * settle;
        p[HandLX] = 143; p[HandLY] = 201 - settle * 12;
        p[Y] -= 2 * settle;
        break;
    }
    case Pet::Error: {
        const float tremble = gesture(t, 1.0f, .12f, .22f, .4f);
        p[Alarm] = 1; p[Pupil] = .6f; p[EyeL] = p[EyeR] = 1.1f;
        p[BrowL] = -15; p[BrowR] = 15; p[BrowLift] = -3;
        p[Smile] = -14; p[MouthWidth] = 49;
        p[X] = std::sin(t * 30) * tremble * 3;
        p[HandLX] = 138; p[HandRX] = 342;
        p[HandLY] = 213 + tremble * 3; p[HandRY] = 213 - tremble * 3;
        break;
    }
    case Pet::Paused:
        p[EyeL] = p[EyeR] = .38f; p[Sleep] = .45f;
        p[BrowL] = -6; p[BrowR] = 6; p[BrowLift] = 7;
        p[GazeY] = 8; p[GazeX] = 0; p[Smile] = 0; p[MouthWidth] = 29;
        p[Y] += 5; p[Roll] = -.022f + breath * .006f;
        p[HandLX] = 211; p[HandRX] = 270; p[HandLY] = p[HandRY] = 240;
        break;
    case Pet::Offline:
        p[EyeL] = p[EyeR] = .16f; p[Sleep] = 1;
        p[BrowL] = -13; p[BrowR] = 13; p[BrowLift] = 11;
        p[GazeY] = 10; p[GazeX] = 0; p[Smile] = -9; p[MouthWidth] = 33;
        p[Y] += 11; p[Stretch] -= .035f; p[Roll] = .035f;
        p[HandLY] = p[HandRY] = 262;
        break;
    case Pet::Searching:
        p[Focus] = 1;
        p[GazeX] = t < 2.5f ? -15 : t < 4.5f ? 15 : 0;
        p[GazeY] = -5; p[EyeL] = 1.1f; p[EyeR] = .87f;
        p[BrowL] = -10; p[BrowR] = -3; p[BrowLift] = -5;
        p[Roll] = p[GazeX] * .002f;
        p[HandRX] = 305; p[HandRY] = 89 + breath;
        break;
    default: break;
    }
    p[BodyRoll] = -p[Roll] * .4f;
    if (theme_ == Pet::Mechanical) {
        p[Stretch] = 1;
        p[Y] *= .55f; p[Roll] *= .55f;
    } else if (theme_ == Pet::Glass) {
        p[Stretch] += breath * .004f;
    }
    return p;
}

float Motion::blink(unsigned eye) const
{
    if (state_ == Pet::Offline || state_ == Pet::Paused) return 1;
    const float t = blink_age_ - (eye ? .012f : 0);
    if (t < 0 || t >= .24f) return 1;
    if (t < .07f) return 1 - smooth(t / .07f);
    if (t < .10f) return 0;
    return smooth((t - .10f) / .14f);
}

void Motion::advance(Pet::State state, float dt, float transition_seconds, float touch)
{
    if (!std::isfinite(dt) || dt <= 0) return;
    dt = std::min(dt, .10f);
    if (state != state_ || !initialized_) {
        state_ = state;
        state_time_ = 0; phrase_time_ = 0; variant_ = 0;
        phrase_duration_ = 7.8f + random_unit() * 2.0f;
    }
    clock_ += dt; state_time_ += dt; phrase_time_ += dt;
    blink_age_ += dt; next_blink_ -= dt;
    if (next_blink_ <= 0) {
        blink_age_ = 0;
        next_blink_ = 2.9f + random_unit() * 3.7f;
    }
    if (phrase_time_ >= phrase_duration_) {
        phrase_time_ -= phrase_duration_;
        // Each long-lived state traverses all five phrases without immediate repeats.
        variant_ = (variant_ + (theme_ == Pet::Glass ? 2u : 1u)) % 5u;
        phrase_duration_ = 7.5f + random_unit() * 3.0f;
    }
    Pose next = target();
    next[Y] -= touch * 5;
    next[Stretch] -= touch * .035f;
    next[BrowLift] -= touch * 5;
    if (!initialized_) {
        pose_ = next;
        initialized_ = true;
    }
    // Exact critically damped integration is stable across 16-100 ms frames.
    // Fast gaze, medium face, and delayed hands/head provide follow-through.
    const float speed = .65f / std::clamp(transition_seconds, .2f, 2.0f);
    for (unsigned i = 0; i < Count; ++i) {
        float omega = 14;
        if (i == GazeX || i == GazeY) omega = 38;
        else if (i == EyeL || i == EyeR) omega = 24;
        else if (i <= Stretch || i == BodyRoll || i == BodyY) omega = 9;
        else if (i >= HandLX && i <= HandRY) omega = 11;
        omega *= speed;
        const float offset = pose_[i] - next[i];
        const float term = velocity_[i] + omega * offset;
        const float decay = std::exp(-omega * dt);
        pose_[i] = next[i] + (offset + term * dt) * decay;
        velocity_[i] = (velocity_[i] - omega * term * dt) * decay;
    }
}
} // namespace Character
