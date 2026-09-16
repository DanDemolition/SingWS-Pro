# Live mic inputs from the mixer (Prompt 7)

Status: **design + diagnostic prototype built, untested on hardware.** Off by
default. Nothing here feeds transitions yet (that's Prompt 8), sends audio back,
or records anything.

## Targets

1. **Soundcraft Ui24R (primary; not owned yet).** 32×32 USB-B multitrack
   interface, so Singer 1, Singer 2 and Host each arrive on their own USB input
   channel. SingWS preset assumes USB input N = mixer channel N (Singer 1 = 1,
   Singer 2 = 2, Host = 3). **Confirm against the Ui24R's USB routing page** and
   edit in the dialog.
2. **Soundcraft Signature 10 (backup).** Two USB channels switched to Aux 1
   (both singers) / Aux 2 (host). Singers can't be separated, so no duet pitch
   work on this route.
3. **Signature 22 MTK.** Multitrack; example preset Host 5 / Singer 6 / Singer 7.

## Signal path and feedback prevention

- Dry mics always stay in the mixer → Main L/R. SingWS only reads copies.
- Never route the USB return (SingWS playback) into the Aux buses or channels
  that feed these inputs. On the Signature 10, keep both Aux knobs on the USB
  return channel fully down.
- The dialog shows this warning. `suspect_noise` (constant high level for 5 s)
  flags hum, feedback or an open mic in a loud room.

## Architecture

```
Ui24R USB inputs ──> SingWSMicMeter (separate process, Core Audio IOProc)
                          │  JSON lines: hello / levels (per channel RMS+peak dB, 10×/s)
                          │              heartbeat / device_lost / format_changed / error
                          ▼
                     MicMonitor (daemon thread, sound_monitor supervision)
                          │  MicActivity: per-role state with hysteresis
                          ▼
            Live Mic Inputs dialog (meters, calibration)      [Prompt 8: observer]
```

- **Separate process:** a mic or driver problem can't take SingWS Pro down. It
  exits on stdin EOF, so it never outlives the app.
- **Levels only:** RMS and peak per 100 ms. No samples leave the helper; nothing
  is written to disk.
- **Separation from vocal effects:** effects (Prompt 10) need 64–128-sample
  round-trip audio and will be a different helper with its own callback. This
  meter's latency (~100 ms) and failure handling are deliberately unsuitable
  for, and independent of, effects.

## Device identity, mapping and formats

- Devices are selected by Core Audio **UID** (stable across reboots and ports),
  with the name shown for humans. `SingWSMicMeter --list-devices` lists
  input-capable devices, input channel count and sample rate.
- Channel numbers are 1-based input channels of that device. `MicInputConfig`
  rejects missing devices, unmapped roles, duplicate channels, channels beyond
  the device's inputs, and mixing combined and separate singer roles.
- The meter reads the device's native format (float32 from the HAL) and computes
  levels at whatever the sample rate is, so no resampling is needed for metering.

## Same device for playback and capture

Recommended: SingWS Pro output **and** mic inputs on the same Ui24R. One clock,
no drift. If output goes elsewhere (e.g. the Mac's headphone jack), metering
still works, because levels don't need sample-accurate alignment. Future
features that combine mic and program audio in time would need an aggregate
device with drift compensation.

## Loss, reconnect, sleep/wake, format changes

- `kAudioDevicePropertyDeviceIsAlive` → `device_lost`; sample-rate change →
  `format_changed`. The helper exits; `MicMonitor` treats both as **transient**:
  it drops all mic evidence immediately, shows "waiting", and retries every 2 s
  without counting a failure. Replugging resumes metering.
- Crashes, garbage output, missing heartbeats and RSS/CPU over budget count as
  failures; 3 failures → bypassed for the session.
- Sleep/wake: the device normally goes not-alive → the same transient path.
  **Unverified on hardware.**

## Permissions

- `NSMicrophoneUsageDescription` (Info.plist) and
  `com.apple.security.device.audio-input` (entitlements) added. The prompt
  appears the first time meters start, never at app launch.
- The laptop's built-in microphone is never opened automatically. Only the
  device the host picks is used, and only after pressing **Start Meters**.

## Activity states (per role)

`silent` · `active` (above floor +12 dB for 250 ms) · `sustained` (active
≥ 1.5 s) · `clipping` (peak ≥ −1 dBFS) · `suspect_noise` · `stale` (no report
> 1 s). Closing needs 600 ms below floor +8 dB. **Calibrate Quiet Level**
records 3 s of idle mics and stores each channel's median as its floor.
These are level features only; they don't claim to tell singing from speech.

## Settings

- `ia_mic_awareness_enabled` (default **false**)
- `ia_mic_config`: `{preset, device_uid, device_name, roles{role: channel}, noise_floor_db{role: dB}}`
- UI: Settings → **Live Mic Inputs…** (next to Record transition diagnostics)

## Manual test checklist — Ui24R (when it arrives)

1. Speakers/amps **down**. Ui24R connected by USB-B; select it as SingWS Pro output.
2. Settings → Live Mic Inputs… → Mixer: Ui24R → Refresh → pick the Ui24R.
3. Confirm the channel count shown matches the Ui24R USB config; set Singer 1/2/Host to the real channels.
4. Start Meters → approve the microphone prompt.
5. Tap each mic in turn: only its row moves; state goes ACTIVE, then SUSTAINED on a held note.
6. All quiet → Calibrate → Save. Rows read "quiet".
7. Unplug USB during a song: playback continues on the fallback path; dialog says waiting; replug → meters resume.
8. Change the Ui24R sample rate: same waiting/resume behavior.
9. Sleep/wake the Mac with meters on: no crash; meters resume or say waiting.
10. Confirm in Activity Monitor: `SingWSMicMeter` < 10 % CPU, < 80 MB; it disappears when the dialog closes or the app quits.
11. Raise PA slowly; confirm no feedback path (USB return not in any aux feeding inputs).

## Manual test checklist — Signature 10

1. Speakers down. Mixer USB OUT switch → Aux 1–2. Singer channels → Aux 1, host channel → Aux 2.
2. **USB return channel Aux 1 and Aux 2 knobs fully down**; karaoke/BGM channel aux sends down.
3. Dialog → Mixer: Signature 10 → device → Singers = 1, Host = 2 → Start Meters.
4. Sing on either singer mic: only "Singers (combined)" moves. Talk on host: only "Host" moves.
5. Play a song through SingWS Pro: neither row should move (proves no return leaking into the auxes).
6. Repeat unplug / sleep checks from the Ui24R list.
