#include "vfx_dsp.h"

#include <math.h>
#include <string.h>

/* Soft knee limiter. A hard clip on a vocal in a room is worse than the
 * overload it protects against, so we squash rather than truncate. */
static inline float vfx_limit(float x, int *clipped)
{
    const float threshold = 0.891251f; /* -1 dBFS */
    float a = fabsf(x);
    if (a <= threshold) return x;
    *clipped = 1;
    float over = a - threshold;
    float squashed = threshold + over / (1.0f + over * 4.0f);
    if (squashed > 0.999f) squashed = 0.999f;
    return x < 0.0f ? -squashed : squashed;
}

void vfx_init(vfx_state *s, double sample_rate, int channels)
{
    memset(s, 0, sizeof(*s));
    s->sample_rate = sample_rate > 0.0 ? sample_rate : 48000.0;
    s->channels = channels > 0 ? channels : 2;

    vfx_params p = { .wet_gain = 1.0f, .enabled = 0 };
    s->slots[0] = p;
    s->slots[1] = p;
    atomic_store_explicit(&s->live, 0, memory_order_release);

    /* Start bypassed and silent: nothing is ever enabled by default. */
    s->gain_now = p.wet_gain;
    s->fade_now = 0.0f;

    float fade_frames = (float)(s->sample_rate * (VFX_FADE_MS / 1000.0));
    if (fade_frames < 1.0f) fade_frames = 1.0f;
    s->fade_step = 1.0f / fade_frames;

    /* Parameter glide over the same span; long enough to kill zipper noise,
     * short enough that a fader move feels immediate. */
    s->gain_step = 1.0f / fade_frames;

    atomic_store_explicit(&s->frames, 0, memory_order_relaxed);
    atomic_store_explicit(&s->clipped, 0, memory_order_relaxed);
    atomic_store_explicit(&s->overruns, 0, memory_order_relaxed);
    atomic_store_explicit(&s->param_updates, 0, memory_order_relaxed);
    atomic_store_explicit(&s->peak_milli_db, -120000, memory_order_relaxed);
}

void vfx_publish(vfx_state *s, vfx_params p)
{
    if (p.wet_gain < 0.0f) p.wet_gain = 0.0f;
    if (p.wet_gain > 4.0f) p.wet_gain = 4.0f;
    p.enabled = p.enabled ? 1 : 0;

    /* Write the slot the callback is NOT reading, then flip. The callback can
     * only ever observe a fully written snapshot. */
    int live = atomic_load_explicit(&s->live, memory_order_acquire);
    int next = live ^ 1;
    s->slots[next] = p;
    atomic_store_explicit(&s->live, next, memory_order_release);
    atomic_fetch_add_explicit(&s->param_updates, 1, memory_order_relaxed);
}

void vfx_process(vfx_state *s, float *buf, int frames)
{
    if (!buf || frames <= 0) return;

    int live = atomic_load_explicit(&s->live, memory_order_acquire);
    const vfx_params p = s->slots[live];

    const int ch = s->channels;
    const float target_fade = p.enabled ? 1.0f : 0.0f;
    const float target_gain = p.wet_gain;

    int clipped_any = 0;
    float peak = 0.0f;

    for (int i = 0; i < frames; ++i) {
        /* Glide, never jump. */
        if (s->fade_now < target_fade) {
            s->fade_now += s->fade_step;
            if (s->fade_now > target_fade) s->fade_now = target_fade;
        } else if (s->fade_now > target_fade) {
            s->fade_now -= s->fade_step;
            if (s->fade_now < target_fade) s->fade_now = target_fade;
        }
        if (s->gain_now < target_gain) {
            s->gain_now += s->gain_step;
            if (s->gain_now > target_gain) s->gain_now = target_gain;
        } else if (s->gain_now > target_gain) {
            s->gain_now -= s->gain_step;
            if (s->gain_now < target_gain) s->gain_now = target_gain;
        }

        const float g = s->gain_now * s->fade_now;
        for (int c = 0; c < ch; ++c) {
            size_t idx = (size_t)i * (size_t)ch + (size_t)c;
            int clipped = 0;
            float v = vfx_limit(buf[idx] * g, &clipped);
            clipped_any |= clipped;
            buf[idx] = v;
            float a = fabsf(v);
            if (a > peak) peak = a;
        }
    }

    atomic_fetch_add_explicit(&s->frames, (uint64_t)frames, memory_order_relaxed);
    if (clipped_any)
        atomic_fetch_add_explicit(&s->clipped, 1, memory_order_relaxed);

    int milli = peak > 0.0f ? (int)(20.0f * log10f(peak) * 1000.0f) : -120000;
    atomic_store_explicit(&s->peak_milli_db, milli, memory_order_relaxed);
}

void vfx_note_overrun(vfx_state *s)
{
    atomic_fetch_add_explicit(&s->overruns, 1, memory_order_relaxed);
}

int vfx_is_silent(const vfx_state *s)
{
    return s->fade_now <= 0.0f;
}
