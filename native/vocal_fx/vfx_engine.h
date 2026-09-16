/* Core Audio glue for the vocal-effects helper. The render callback lives here,
 * in C, deliberately: Swift's ARC can retain/release, allocate and lock on any
 * object touch, none of which is permissible in a real-time callback.
 * See docs/intelligent_audio/VOCAL_EFFECTS_ARCHITECTURE.md §3-4. */
#ifndef SINGWS_VFX_ENGINE_H
#define SINGWS_VFX_ENGINE_H

#include <stdint.h>
#include <stddef.h>

typedef struct vfx_engine vfx_engine;

/* All setup work happens here: device binding, buffer size, unit
 * instantiation and every allocation the callback will ever need. */
vfx_engine *vfx_engine_create(const char *device_uid,
                              const int *in_channels, int in_channel_count,
                              int frames, int use_reverb,
                              char *err, size_t err_len);

int  vfx_engine_start(vfx_engine *e, char *err, size_t err_len);
void vfx_engine_stop(vfx_engine *e);
void vfx_engine_destroy(vfx_engine *e);

/* Control thread only. Publishes an immutable snapshot. */
void vfx_engine_publish(vfx_engine *e, float wet_gain, int enabled);

void vfx_engine_stats(vfx_engine *e,
                      uint64_t *frames, uint64_t *clipped, uint64_t *overruns,
                      int *peak_milli_db, int *silent);

/* Estimated round trip from the device's reported latencies and buffer size.
 * This is an estimate, not a measurement -- a true figure needs a loopback. */
double vfx_engine_estimated_latency_ms(vfx_engine *e);
double vfx_engine_sample_rate(vfx_engine *e);
int    vfx_engine_frames(vfx_engine *e);

/* 0 once Core Audio reports the device gone or its format changed. */
int vfx_engine_device_ok(vfx_engine *e);
int vfx_engine_format_changed(vfx_engine *e);

#endif
