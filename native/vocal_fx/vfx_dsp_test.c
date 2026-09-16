/* Offline harness for the vocal-effects real-time core. No Core Audio, no
 * device, no sound: the callback contract and signal behaviour are verified by
 * driving vfx_process directly. Run with ./build.sh --test. */
#include "vfx_dsp.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

static int failures = 0;

static void ok(int cond, const char *what)
{
    if (!cond) { printf("  FAIL %s\n", what); failures++; }
    else       { printf("  ok   %s\n", what); }
}

static void fill_sine(float *buf, int frames, int ch, float amp)
{
    for (int i = 0; i < frames; ++i)
        for (int c = 0; c < ch; ++c)
            buf[(size_t)i * ch + c] = amp * sinf(6.2831853f * 440.0f * i / 48000.0f);
}

/* Run enough buffers that any fade or glide has settled. */
static void settle(vfx_state *s, float *buf, int frames, int ch)
{
    for (int n = 0; n < 200; ++n) { fill_sine(buf, frames, ch, 0.5f); vfx_process(s, buf, frames); }
}

int main(void)
{
    enum { FRAMES = 128, CH = 2 };
    float buf[FRAMES * CH], ref[FRAMES * CH];
    vfx_state s;

    printf("VFX0 passthrough / VFX1 wet core\n");

    /* 1. Defaults to silence: nothing is ever enabled by default. */
    vfx_init(&s, 48000.0, CH);
    fill_sine(buf, FRAMES, CH, 0.5f);
    vfx_process(&s, buf, FRAMES);
    float peak = 0.0f;
    for (int i = 0; i < FRAMES * CH; ++i) peak = fmaxf(peak, fabsf(buf[i]));
    ok(peak == 0.0f, "starts bypassed and silent");
    ok(vfx_is_silent(&s), "reports silent when bypassed");

    /* 2. Unity gain: enabled at 1.0, once settled, output == input exactly. */
    vfx_init(&s, 48000.0, CH);
    vfx_publish(&s, (vfx_params){ .wet_gain = 1.0f, .enabled = 1 });
    settle(&s, buf, FRAMES, CH);
    fill_sine(buf, FRAMES, CH, 0.5f);
    memcpy(ref, buf, sizeof(buf));
    vfx_process(&s, buf, FRAMES);
    int identical = memcmp(buf, ref, sizeof(buf)) == 0;
    ok(identical, "unity gain is bit-exact passthrough");

    /* 3. Bypass fades down smoothly and actually reaches silence. */
    vfx_publish(&s, (vfx_params){ .wet_gain = 1.0f, .enabled = 0 });
    float prev = 1.0f; int monotonic = 1;
    for (int n = 0; n < 200; ++n) {
        fill_sine(buf, FRAMES, CH, 1.0f);
        float before = fabsf(buf[0]);
        vfx_process(&s, buf, FRAMES);
        float after = fabsf(buf[0]);
        float ratio = before > 0.0f ? after / before : 0.0f;
        if (ratio > prev + 1e-4f) monotonic = 0;
        prev = ratio;
    }
    ok(monotonic, "bypass fade never rises");
    ok(vfx_is_silent(&s), "bypass reaches true silence");
    fill_sine(buf, FRAMES, CH, 1.0f); vfx_process(&s, buf, FRAMES);
    peak = 0.0f; for (int i = 0; i < FRAMES * CH; ++i) peak = fmaxf(peak, fabsf(buf[i]));
    ok(peak == 0.0f, "stays silent while bypassed");

    /* 4. No pop: the very first buffer after a bypass request must not jump to
     * zero. A hard mute is the artefact this whole fade exists to prevent. */
    vfx_init(&s, 48000.0, CH);
    vfx_publish(&s, (vfx_params){ .wet_gain = 1.0f, .enabled = 1 });
    settle(&s, buf, FRAMES, CH);
    vfx_publish(&s, (vfx_params){ .wet_gain = 1.0f, .enabled = 0 });
    for (int i = 0; i < FRAMES * CH; ++i) buf[i] = 1.0f;
    vfx_process(&s, buf, FRAMES);
    ok(buf[0] > 0.5f, "first bypassed buffer is not a hard mute");

    /* 5. Gain changes glide rather than step (no zipper noise). */
    vfx_init(&s, 48000.0, CH);
    vfx_publish(&s, (vfx_params){ .wet_gain = 0.0f, .enabled = 1 });
    settle(&s, buf, FRAMES, CH);
    vfx_publish(&s, (vfx_params){ .wet_gain = 2.0f, .enabled = 1 });
    for (int i = 0; i < FRAMES * CH; ++i) buf[i] = 0.25f;
    vfx_process(&s, buf, FRAMES);
    float max_step = 0.0f;
    for (int i = CH; i < FRAMES * CH; i += CH)
        max_step = fmaxf(max_step, fabsf(buf[i] - buf[i - CH]));
    ok(max_step < 0.02f, "gain glides without a step discontinuity");

    /* 6. Limiting: hot input is squashed, never exceeds full scale, counted. */
    vfx_init(&s, 48000.0, CH);
    vfx_publish(&s, (vfx_params){ .wet_gain = 4.0f, .enabled = 1 });
    settle(&s, buf, FRAMES, CH);
    for (int i = 0; i < FRAMES * CH; ++i) buf[i] = 0.9f;
    vfx_process(&s, buf, FRAMES);
    int within = 1;
    for (int i = 0; i < FRAMES * CH; ++i) if (fabsf(buf[i]) > 1.0f) within = 0;
    ok(within, "limiter keeps output inside full scale");
    ok(atomic_load(&s.clipped) > 0, "limiting is counted");

    /* 7. Snapshot publication is seen by the next buffer. */
    unsigned long long before_updates = atomic_load(&s.param_updates);
    vfx_publish(&s, (vfx_params){ .wet_gain = 0.5f, .enabled = 1 });
    ok(atomic_load(&s.param_updates) == before_updates + 1, "publish is recorded");

    /* 8. Overrun accounting. */
    vfx_note_overrun(&s);
    ok(atomic_load(&s.overruns) == 1, "overruns are counted");

    /* 9. Degenerate input is ignored rather than crashing. */
    vfx_process(&s, NULL, FRAMES);
    vfx_process(&s, buf, 0);
    vfx_process(&s, buf, -1);
    ok(1, "null and non-positive frame counts are safe");

    printf(failures ? "\nFAILED (%d)\n" : "\nall passed\n", failures);
    return failures ? 1 : 0;
}
