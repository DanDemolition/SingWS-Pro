#include "vfx_engine.h"
#include "vfx_dsp.h"

#include <AudioToolbox/AudioToolbox.h>
#include <CoreAudio/CoreAudio.h>
#include <mach/mach_time.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

#define VFX_MAX_IN_CH 8

struct vfx_engine {
    AudioUnit        io;        /* HAL I/O: input on bus 1, output on bus 0 */
    AudioUnit        reverb;    /* optional; NULL for VFX0 passthrough */
    AudioDeviceID    device;
    double           sample_rate;
    UInt32           frames;
    int              use_reverb;

    int              in_channels[VFX_MAX_IN_CH];
    int              in_channel_count;

    /* Preallocated. The callback never allocates. */
    AudioBufferList *in_abl;
    float           *in_scratch;   /* interleaved capture */
    float           *mix;          /* frames * 2, the wet bus */
    UInt32           in_device_channels;

    vfx_state        dsp;

    double           latency_ms;
    _Atomic int      device_ok;
    _Atomic int      format_changed;
    _Atomic int      running;

    double           budget_ns;    /* per-buffer deadline for overrun counting */
    uint64_t         timebase_num, timebase_den;
};

static OSStatus set_prop(AudioUnit u, AudioUnitPropertyID id, AudioUnitScope scope,
                         AudioUnitElement el, const void *d, UInt32 n)
{
    return AudioUnitSetProperty(u, id, scope, el, d, n);
}

static AudioDeviceID device_for_uid(const char *uid)
{
    if (!uid || !*uid) {
        AudioDeviceID dev = kAudioObjectUnknown;
        UInt32 size = sizeof(dev);
        AudioObjectPropertyAddress a = { kAudioHardwarePropertyDefaultInputDevice,
                                         kAudioObjectPropertyScopeGlobal,
                                         kAudioObjectPropertyElementMain };
        AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &size, &dev);
        return dev;
    }
    CFStringRef want = CFStringCreateWithCString(NULL, uid, kCFStringEncodingUTF8);
    AudioObjectPropertyAddress a = { kAudioHardwarePropertyDevices,
                                     kAudioObjectPropertyScopeGlobal,
                                     kAudioObjectPropertyElementMain };
    UInt32 size = 0;
    AudioDeviceID found = kAudioObjectUnknown;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &a, 0, NULL, &size) == noErr) {
        int count = (int)(size / sizeof(AudioDeviceID));
        AudioDeviceID *ids = (AudioDeviceID *)calloc((size_t)count, sizeof(AudioDeviceID));
        if (ids && AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &size, ids) == noErr) {
            for (int i = 0; i < count && found == kAudioObjectUnknown; ++i) {
                CFStringRef u = NULL; UInt32 s = sizeof(u);
                AudioObjectPropertyAddress ua = { kAudioDevicePropertyDeviceUID,
                                                  kAudioObjectPropertyScopeGlobal,
                                                  kAudioObjectPropertyElementMain };
                if (AudioObjectGetPropertyData(ids[i], &ua, 0, NULL, &s, &u) == noErr && u) {
                    if (CFStringCompare(u, want, 0) == kCFCompareEqualTo) found = ids[i];
                    CFRelease(u);
                }
            }
        }
        free(ids);
    }
    CFRelease(want);
    return found;
}

static double device_latency_frames(AudioDeviceID dev, AudioObjectPropertyScope scope)
{
    double total = 0;
    UInt32 v = 0, size = sizeof(v);
    AudioObjectPropertyAddress a = { kAudioDevicePropertyLatency, scope, kAudioObjectPropertyElementMain };
    if (AudioObjectGetPropertyData(dev, &a, 0, NULL, &size, &v) == noErr) total += v;
    a.mSelector = kAudioDevicePropertySafetyOffset; size = sizeof(v);
    if (AudioObjectGetPropertyData(dev, &a, 0, NULL, &size, &v) == noErr) total += v;
    return total;
}

/* Pulls the captured input for the reverb unit. Preconfigured; no allocation. */
static OSStatus reverb_input_cb(void *ref, AudioUnitRenderActionFlags *flags,
                                const AudioTimeStamp *ts, UInt32 bus,
                                UInt32 frames, AudioBufferList *io)
{
    (void)flags; (void)ts; (void)bus;
    vfx_engine *e = (vfx_engine *)ref;
    for (UInt32 c = 0; c < io->mNumberBuffers; ++c) {
        float *dst = (float *)io->mBuffers[c].mData;
        for (UInt32 i = 0; i < frames; ++i)
            dst[i] = e->mix[(size_t)i * 2 + (c % 2)];
    }
    return noErr;
}

/* The render callback. Callback contract: no allocation, no locks, no logging,
 * no I/O, no Swift, work bounded by the frame count. */
static OSStatus render_cb(void *ref, AudioUnitRenderActionFlags *flags,
                          const AudioTimeStamp *ts, UInt32 bus,
                          UInt32 frames, AudioBufferList *io)
{
    vfx_engine *e = (vfx_engine *)ref;
    uint64_t t0 = mach_absolute_time();

    if (frames > e->frames) {           /* never grow buffers here */
        for (UInt32 c = 0; c < io->mNumberBuffers; ++c)
            memset(io->mBuffers[c].mData, 0, io->mBuffers[c].mDataByteSize);
        vfx_note_overrun(&e->dsp);
        return noErr;
    }

    /* 1. Capture. */
    for (UInt32 c = 0; c < e->in_abl->mNumberBuffers; ++c) {
        e->in_abl->mBuffers[c].mDataByteSize = frames * (UInt32)sizeof(float);
        e->in_abl->mBuffers[c].mData = e->in_scratch + (size_t)c * e->frames;
    }
    OSStatus st = AudioUnitRender(e->io, flags, ts, 1, frames, e->in_abl);
    if (st != noErr) {
        for (UInt32 c = 0; c < io->mNumberBuffers; ++c)
            memset(io->mBuffers[c].mData, 0, io->mBuffers[c].mDataByteSize);
        return noErr;                    /* silence, never noise */
    }

    /* 2. Fold the selected source channels down to the stereo wet bus. */
    memset(e->mix, 0, (size_t)frames * 2 * sizeof(float));
    int n = e->in_channel_count > 0 ? e->in_channel_count : 1;
    for (int k = 0; k < n; ++k) {
        int ch = e->in_channel_count > 0 ? e->in_channels[k] - 1 : 0;
        if (ch < 0 || (UInt32)ch >= e->in_abl->mNumberBuffers) continue;
        const float *src = (const float *)e->in_abl->mBuffers[ch].mData;
        for (UInt32 i = 0; i < frames; ++i) {
            float v = src[i] / (float)n;
            e->mix[(size_t)i * 2 + 0] += v;
            e->mix[(size_t)i * 2 + 1] += v;
        }
    }

    /* 3. Effect. VFX0 has none; VFX1 renders Reverb2 over the same bus. */
    if (e->use_reverb && e->reverb) {
        AudioBufferList *out = io;
        AudioUnitRenderActionFlags f = 0;
        if (AudioUnitRender(e->reverb, &f, ts, 0, frames, out) == noErr) {
            for (UInt32 i = 0; i < frames; ++i)
                for (int c = 0; c < 2 && (UInt32)c < out->mNumberBuffers; ++c)
                    e->mix[(size_t)i * 2 + c] = ((const float *)out->mBuffers[c].mData)[i];
        }
    }

    /* 4. Wet gain, bypass fade, limiting. */
    vfx_process(&e->dsp, e->mix, (int)frames);

    /* 5. Wet only. The dry voice never came through here and never leaves here. */
    for (UInt32 c = 0; c < io->mNumberBuffers; ++c) {
        float *dst = (float *)io->mBuffers[c].mData;
        for (UInt32 i = 0; i < frames; ++i)
            dst[i] = e->mix[(size_t)i * 2 + (c % 2)];
    }

    uint64_t dt = mach_absolute_time() - t0;
    double ns = (double)dt * (double)e->timebase_num / (double)e->timebase_den;
    if (ns > e->budget_ns) vfx_note_overrun(&e->dsp);
    (void)bus;
    return noErr;
}

static OSStatus device_listener(AudioObjectID obj, UInt32 n,
                                const AudioObjectPropertyAddress *addrs, void *ref)
{
    (void)obj;
    vfx_engine *e = (vfx_engine *)ref;
    for (UInt32 i = 0; i < n; ++i) {
        if (addrs[i].mSelector == kAudioDevicePropertyDeviceIsAlive)
            atomic_store(&e->device_ok, 0);
        else if (addrs[i].mSelector == kAudioDevicePropertyNominalSampleRate)
            atomic_store(&e->format_changed, 1);
    }
    return noErr;
}

vfx_engine *vfx_engine_create(const char *device_uid,
                              const int *in_channels, int in_channel_count,
                              int frames, int use_reverb,
                              char *err, size_t err_len)
{
    if (frames <= 0) frames = 128;
    vfx_engine *e = (vfx_engine *)calloc(1, sizeof(*e));
    if (!e) { snprintf(err, err_len, "out of memory"); return NULL; }
    e->frames = (UInt32)frames;
    e->use_reverb = use_reverb ? 1 : 0;
    atomic_store(&e->device_ok, 1);
    atomic_store(&e->format_changed, 0);

    mach_timebase_info_data_t tb; mach_timebase_info(&tb);
    e->timebase_num = tb.numer; e->timebase_den = tb.denom;

    e->in_channel_count = in_channel_count > VFX_MAX_IN_CH ? VFX_MAX_IN_CH : in_channel_count;
    for (int i = 0; i < e->in_channel_count; ++i) e->in_channels[i] = in_channels[i];

    e->device = device_for_uid(device_uid);
    if (e->device == kAudioObjectUnknown) {
        snprintf(err, err_len, "audio device not found");
        free(e); return NULL;
    }

    AudioComponentDescription d = { kAudioUnitType_Output, kAudioUnitSubType_HALOutput,
                                    kAudioUnitManufacturer_Apple, 0, 0 };
    AudioComponent comp = AudioComponentFindNext(NULL, &d);
    if (!comp || AudioComponentInstanceNew(comp, &e->io) != noErr) {
        snprintf(err, err_len, "cannot create HAL I/O unit"); free(e); return NULL;
    }

    UInt32 one = 1, zero = 0;
    set_prop(e->io, kAudioOutputUnitProperty_EnableIO, kAudioUnitScope_Input,  1, &one,  sizeof(one));
    set_prop(e->io, kAudioOutputUnitProperty_EnableIO, kAudioUnitScope_Output, 0, &one,  sizeof(one));
    (void)zero;
    if (set_prop(e->io, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0,
                 &e->device, sizeof(e->device)) != noErr) {
        snprintf(err, err_len, "cannot bind device"); AudioComponentInstanceDispose(e->io);
        free(e); return NULL;
    }

    UInt32 buf = e->frames;
    AudioObjectPropertyAddress ba = { kAudioDevicePropertyBufferFrameSize,
                                      kAudioObjectPropertyScopeGlobal,
                                      kAudioObjectPropertyElementMain };
    AudioObjectSetPropertyData(e->device, &ba, 0, NULL, sizeof(buf), &buf);
    UInt32 sz = sizeof(buf);
    if (AudioObjectGetPropertyData(e->device, &ba, 0, NULL, &sz, &buf) == noErr) e->frames = buf;

    Float64 sr = 48000.0; sz = sizeof(sr);
    AudioObjectPropertyAddress sa = { kAudioDevicePropertyNominalSampleRate,
                                      kAudioObjectPropertyScopeGlobal,
                                      kAudioObjectPropertyElementMain };
    AudioObjectGetPropertyData(e->device, &sa, 0, NULL, &sz, &sr);
    e->sample_rate = sr;

    /* Non-interleaved float for capture; stereo float out. */
    AudioStreamBasicDescription in_fmt; sz = sizeof(in_fmt);
    AudioUnitGetProperty(e->io, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Input, 1, &in_fmt, &sz);
    e->in_device_channels = in_fmt.mChannelsPerFrame ? in_fmt.mChannelsPerFrame : 2;

    AudioStreamBasicDescription f = {0};
    f.mSampleRate = sr; f.mFormatID = kAudioFormatLinearPCM;
    f.mFormatFlags = kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked | kAudioFormatFlagIsNonInterleaved;
    f.mBitsPerChannel = 32; f.mFramesPerPacket = 1; f.mBytesPerFrame = 4; f.mBytesPerPacket = 4;
    f.mChannelsPerFrame = e->in_device_channels;
    set_prop(e->io, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Output, 1, &f, sizeof(f));
    f.mChannelsPerFrame = 2;
    set_prop(e->io, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Input, 0, &f, sizeof(f));

    /* Preallocate everything the callback touches. */
    size_t abl_bytes = sizeof(AudioBufferList) + sizeof(AudioBuffer) * (e->in_device_channels ? e->in_device_channels - 1 : 0);
    e->in_abl = (AudioBufferList *)calloc(1, abl_bytes);
    e->in_scratch = (float *)calloc((size_t)e->in_device_channels * e->frames, sizeof(float));
    e->mix = (float *)calloc((size_t)e->frames * 2, sizeof(float));
    if (!e->in_abl || !e->in_scratch || !e->mix) {
        snprintf(err, err_len, "out of memory"); vfx_engine_destroy(e); return NULL;
    }
    e->in_abl->mNumberBuffers = e->in_device_channels;
    for (UInt32 c = 0; c < e->in_device_channels; ++c) {
        e->in_abl->mBuffers[c].mNumberChannels = 1;
        e->in_abl->mBuffers[c].mDataByteSize = e->frames * (UInt32)sizeof(float);
        e->in_abl->mBuffers[c].mData = e->in_scratch + (size_t)c * e->frames;
    }

    if (e->use_reverb) {
        AudioComponentDescription rd = { kAudioUnitType_Effect, kAudioUnitSubType_Reverb2,
                                         kAudioUnitManufacturer_Apple, 0, 0 };
        AudioComponent rc = AudioComponentFindNext(NULL, &rd);
        if (rc && AudioComponentInstanceNew(rc, &e->reverb) == noErr) {
            AudioStreamBasicDescription rf = f; rf.mChannelsPerFrame = 2;
            set_prop(e->reverb, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Input,  0, &rf, sizeof(rf));
            set_prop(e->reverb, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Output, 0, &rf, sizeof(rf));
            UInt32 mx = e->frames;
            set_prop(e->reverb, kAudioUnitProperty_MaximumFramesPerSlice, kAudioUnitScope_Global, 0, &mx, sizeof(mx));
            AURenderCallbackStruct rcb = { reverb_input_cb, e };
            set_prop(e->reverb, kAudioUnitProperty_SetRenderCallback, kAudioUnitScope_Input, 0, &rcb, sizeof(rcb));
            AudioUnitInitialize(e->reverb);
            AudioUnitSetParameter(e->reverb, kReverb2Param_DryWetMix, kAudioUnitScope_Global, 0, 100.0f, 0);
        } else {
            e->use_reverb = 0;   /* fall back to passthrough rather than fail */
        }
    }

    UInt32 mx = e->frames;
    set_prop(e->io, kAudioUnitProperty_MaximumFramesPerSlice, kAudioUnitScope_Global, 0, &mx, sizeof(mx));
    AURenderCallbackStruct cb = { render_cb, e };
    set_prop(e->io, kAudioUnitProperty_SetRenderCallback, kAudioUnitScope_Input, 0, &cb, sizeof(cb));

    vfx_init(&e->dsp, e->sample_rate, 2);
    e->budget_ns = (double)e->frames / e->sample_rate * 1e9 * 0.6;

    double in_l  = device_latency_frames(e->device, kAudioDevicePropertyScopeInput);
    double out_l = device_latency_frames(e->device, kAudioDevicePropertyScopeOutput);
    e->latency_ms = (in_l + out_l + 2.0 * (double)e->frames) / e->sample_rate * 1000.0;

    AudioObjectPropertyAddress la = { kAudioDevicePropertyDeviceIsAlive,
                                      kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain };
    AudioObjectAddPropertyListener(e->device, &la, device_listener, e);
    la.mSelector = kAudioDevicePropertyNominalSampleRate;
    AudioObjectAddPropertyListener(e->device, &la, device_listener, e);

    if (AudioUnitInitialize(e->io) != noErr) {
        snprintf(err, err_len, "cannot initialise audio unit"); vfx_engine_destroy(e); return NULL;
    }
    return e;
}

int vfx_engine_start(vfx_engine *e, char *err, size_t err_len)
{
    OSStatus st = AudioOutputUnitStart(e->io);
    if (st != noErr) { snprintf(err, err_len, "cannot start audio (%d)", (int)st); return 0; }
    atomic_store(&e->running, 1);
    return 1;
}

void vfx_engine_stop(vfx_engine *e)
{
    if (!e) return;
    if (atomic_exchange(&e->running, 0)) AudioOutputUnitStop(e->io);
}

void vfx_engine_destroy(vfx_engine *e)
{
    if (!e) return;
    vfx_engine_stop(e);
    /* Deregister before the struct is freed. These listeners are installed in
     * create() *before* AudioUnitInitialize, so the failure path here would
     * otherwise leave Core Audio holding a pointer to freed memory and call
     * into it on the next device property change. */
    if (e->device != kAudioObjectUnknown) {
        AudioObjectPropertyAddress la = { kAudioDevicePropertyDeviceIsAlive,
                                          kAudioObjectPropertyScopeGlobal,
                                          kAudioObjectPropertyElementMain };
        AudioObjectRemovePropertyListener(e->device, &la, device_listener, e);
        la.mSelector = kAudioDevicePropertyNominalSampleRate;
        AudioObjectRemovePropertyListener(e->device, &la, device_listener, e);
    }
    if (e->reverb) { AudioUnitUninitialize(e->reverb); AudioComponentInstanceDispose(e->reverb); }
    if (e->io)     { AudioUnitUninitialize(e->io);     AudioComponentInstanceDispose(e->io); }
    free(e->in_abl); free(e->in_scratch); free(e->mix);
    free(e);
}

void vfx_engine_publish(vfx_engine *e, float wet_gain, int enabled)
{
    vfx_params p = { .wet_gain = wet_gain, .enabled = enabled };
    vfx_publish(&e->dsp, p);
}

void vfx_engine_stats(vfx_engine *e, uint64_t *frames, uint64_t *clipped,
                      uint64_t *overruns, int *peak_milli_db, int *silent)
{
    if (frames)        *frames        = atomic_load(&e->dsp.frames);
    if (clipped)       *clipped       = atomic_load(&e->dsp.clipped);
    if (overruns)      *overruns      = atomic_load(&e->dsp.overruns);
    if (peak_milli_db) *peak_milli_db = atomic_load(&e->dsp.peak_milli_db);
    if (silent)        *silent        = vfx_is_silent(&e->dsp);
}

double vfx_engine_estimated_latency_ms(vfx_engine *e) { return e ? e->latency_ms : 0.0; }
double vfx_engine_sample_rate(vfx_engine *e)          { return e ? e->sample_rate : 0.0; }
int    vfx_engine_frames(vfx_engine *e)               { return e ? (int)e->frames : 0; }
int    vfx_engine_device_ok(vfx_engine *e)            { return e ? atomic_load(&e->device_ok) : 0; }
int    vfx_engine_format_changed(vfx_engine *e)       { return e ? atomic_load(&e->format_changed) : 0; }
