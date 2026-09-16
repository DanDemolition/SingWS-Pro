/* SingWS Pro vocal effects — real-time core (VFX0/VFX1).
 *
 * Deliberately free of Core Audio so it can be driven by an offline harness.
 * Everything here is callable from the render callback and obeys the callback
 * contract in docs/intelligent_audio/VOCAL_EFFECTS_ARCHITECTURE.md §4:
 * no allocation, no locks, no logging, no I/O, work bounded by frame count.
 *
 * Wet-only: the caller hands us the already-effected signal and we apply gain,
 * bypass fading and limiting. The dry voice never passes through this code,
 * because it never passes through the computer at all.
 */
#ifndef SINGWS_VFX_DSP_H
#define SINGWS_VFX_DSP_H

#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>

#define VFX_FADE_MS 10.0f /* bypass/engage fade. Never a hard mute: that pops. */

/* Published atomically by the control thread; read by the callback. Immutable
 * once published -- the callback never sees a half-written snapshot. */
typedef struct {
    float wet_gain;   /* linear, 0..4 */
    int   enabled;    /* 0 = bypassed (fade to silence), 1 = active */
} vfx_params;

typedef struct {
    /* Double-buffered snapshots plus the index the callback should read. */
    vfx_params       slots[2];
    _Atomic int      live;

    /* Smoothed state, callback-owned. */
    float            gain_now;
    float            fade_now;     /* 0..1, tracks enabled */
    float            fade_step;    /* per-frame increment for VFX_FADE_MS */
    float            gain_step;    /* per-frame increment for parameter glide */

    double           sample_rate;
    int              channels;

    /* Diagnostics. Atomics so the control thread can read without a lock, and
     * counters only -- never strings, never allocation. */
    _Atomic uint64_t frames;
    _Atomic uint64_t clipped;      /* samples limited */
    _Atomic uint64_t overruns;     /* callback took longer than its budget */
    _Atomic uint64_t param_updates;
    _Atomic int      peak_milli_db; /* wet peak, millibels, for the meter */
} vfx_state;

/* Setup only. Never call from the callback. */
void vfx_init(vfx_state *s, double sample_rate, int channels);

/* Control thread: publish a new snapshot atomically. */
void vfx_publish(vfx_state *s, vfx_params p);

/* Callback: apply wet gain, bypass fade and limiting in place.
 * `buf` is interleaved float, `frames` * channels samples. */
void vfx_process(vfx_state *s, float *buf, int frames);

/* Callback: record that a buffer missed its deadline. */
void vfx_note_overrun(vfx_state *s);

/* Control thread: true once the fade has fully reached silence, so the host
 * knows a bypass is audibly complete rather than merely requested. */
int vfx_is_silent(const vfx_state *s);

#endif /* SINGWS_VFX_DSP_H */
