#region Setup
#region Imports
from utils.codebase import *  # toolbox
import utils.codebase as cb # for access to privates
import pygame
pygame.init()
import traceback
import shutil
import subprocess
import cv2
import random
import ctypes
import csv
import threading
from collections import deque
#endregion

#region Persistent settings and environmental parameters
exp_settings_and_data = {}
exp_root = "./geprest/"

dev = False
reply = input("Demo mode? (y/n): ").strip().lower()
demo = reply in ("y", "yes")
if demo:
    dev = False
reply = input("On grid? (y/n): ").strip().lower()
on_grid = reply in ("y", "yes")
batch = 'dev' if dev else ('demo' if demo else 'test') #DEV: placeholder name here for now
exp_settings_and_data['batch'] = batch

zoom = 1
exp_settings_and_data['zoom'] = zoom
font_size = int(22 * zoom)

ui_font = pygame.font.SysFont("bahnschrift", int(font_size * zoom), bold = False)

clock = pygame.time.Clock()

window_w = pygame.display.Info().current_w
window_h = pygame.display.Info().current_h

if dev:
    w = window_w * zoom
    h = window_h * zoom
else:
    w = window_w
    h = window_h

exp_settings_and_data['size'] = {'w':w, 'h':h}
#endregion

#region Run specific parameters & flags

#region Subject related
subject_data = {}

if dev or demo:
    subject = 'Developer'
    stimulus_set = 1
else:
    subject = input("Subject? ")
    while True:
        stimulus_set = input("Stimulus set? (number from 1 to 4): ").strip()
        if stimulus_set in {"1", "2", "3", "4"}:
            stimulus_set = int(stimulus_set)
            break
        print("Please enter 1, 2, 3, or 4.")
exp_settings_and_data['subject'] = subject
exp_settings_and_data['stimulus_set'] = stimulus_set

run_label = f"{batch}_{subject}"
exp_settings_and_data['run_label'] = run_label

output_path = f'{exp_root}/outputs/{batch}/{subject}'
if os.path.exists(output_path):
    if not (dev or demo):
        reply = input(f"Data already exists for this batch/subject pair. Do you want to overwrite it? (y/n): ").strip().lower()
        if reply not in ("y", "yes"):
            raise RuntimeError("Data overwrite cancel.")
    shutil.rmtree(output_path)
log_path = f'{output_path}/log.jsonl'
#endregion

#region Experiment structure
modality_order = random.sample(['aud', 'vis'], k=2)
exp_settings_and_data['modality_order'] = modality_order
phase_order = ['baseline','pattern']

test_conditions = { # hardcode here, select set based on stimulus_set
    'baseline_aud':['1','2'],
    'baseline_vis':['1','2'],
    'pattern_aud': ['1','2'],
    'pattern_vis': ['1','2']
}

condition_orderings = {
    'baseline_aud': [0, 1],
    'baseline_vis': [0, 1],
    'pattern_aud': [0, 1],
    'pattern_vis': [0, 1]
}

if not (demo or dev):
    reply = input("Randomize condition presentation? (y/n): ").strip().lower()
    if reply in ("y", "yes"):
        for key in condition_orderings:
            random.shuffle(condition_orderings[key])

def generate_exp_structure(modality_order, test_conditions, condition_orderings):
    def practice_and_test_block(mod, phase):
        return [
            {
                'type': s,
                'modifiers': {'modality': mod, 'phase': phase},
                **(
                    {'conditions': [test_conditions[f'{phase}_{mod}'][i] for i in condition_orderings[f'{phase}_{mod}']]}
                    if s == 'test' else {}
                )
            }
            for s in ['practice', 'test']
        ]

    exp_structure = [
        {'type': 'welcome'},
        {'type': 'screener'},
        {'type': 'intro_questionnaire'},
        {'type': 'familiarization', 'modifiers': {'modality': modality_order[0], 'phase': phase_order[0]}}
    ] + practice_and_test_block(modality_order[0], phase_order[0]) + [
        {'type': 'familiarization', 'modifiers': {'modality': modality_order[1], 'phase': phase_order[0]}}
    ] + [
        block for mod, phase in [
            (modality_order[1], phase_order[0]), 
            (modality_order[0], phase_order[1]), 
            (modality_order[1], phase_order[1])
        ] for block in practice_and_test_block(mod, phase)
    ] + [
        {'type': 'outro_questionnaire'},
        {'type': 'thanks'}
    ]

    for stage in exp_structure:
        stage['name'] = ((stage['modifiers']['phase'] +'_') if 'modifiers' in stage else '') + stage['type'] + (('_'+ stage['modifiers']['modality'])if 'modifiers' in stage else '')
    
    return exp_structure

demo_exp = [
    {'type': 'demo_welcome','name':'demo_welcome'},
    {'type': 'demo_stimulus','modifiers': {'modality': 'aud', 'phase': ''},'name':'demo_aud'},
    {'type': 'demo_stimulus','modifiers': {'modality': 'vis', 'phase': ''},'name':'demo_vis'},
    {'type': 'demo_thanks','name':'demo_thanks'}
]

exp_structure = demo_exp if demo else generate_exp_structure(modality_order,test_conditions,condition_orderings)
exp_settings_and_data['exp_structure'] = exp_structure

master_gain_exp = 1
exp_settings_and_data['master_gain'] = master_gain_exp #DEV: define usage
#endregion

#region Declare flags
stage_completed = None
stage_start = None
substage = None # can be 'intro', 'stimulus' or 'repeat'
stimulus_finished = None
stimulus_start = None
repeat_num = None
log_space_press = None
test_condition_index = None
#endregion

#region Device IO
#region Video startup and wrap
"""
After input() before UI
"""
screen = pygame.display.set_mode((w, h), pygame.NOFRAME)
pygame.display.set_caption("GEPREST window")

_loading_base = "Loading stimuli and instructions, please wait"
_loading_phase = 0
_loading_last_update = pygame.time.get_ticks()

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SW_RESTORE = 9
SW_SHOW = 5

def focus_pygame_window(window_title = "GEPREST window", delay=0.3):
    pygame.display.flip()
    time.sleep(delay)

    hwnd = user32.FindWindowW(None, window_title)
    if not hwnd:
        return False

    fg_hwnd = user32.GetForegroundWindow()
    current_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0

    try:
        if fg_tid and fg_tid != current_tid:
            user32.AttachThreadInput(fg_tid, current_tid, True)

        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
        user32.SetActiveWindow(hwnd)

    finally:
        if fg_tid and fg_tid != current_tid:
            user32.AttachThreadInput(fg_tid, current_tid, False)

    return user32.GetForegroundWindow() == hwnd

def draw_loading_screen():
    global _loading_phase, _loading_last_update

    now = pygame.time.get_ticks()
    if now - _loading_last_update >= 400:   # change every 400 ms
        _loading_phase = (_loading_phase + 1) % 4
        _loading_last_update = now

    suffix = ["", ".", "..", "..."][_loading_phase]
    text = _loading_base + suffix

    screen.fill((255, 255, 255))
    surf = ui_font.render(text, True, (0, 0, 0))
    rect = surf.get_rect(center=(w // 2, h // 2))
    screen.blit(surf, rect)
    pygame.display.flip()

_video_state = None          # global dict holding the current clip (or None)

def load_mp4(path):
    """
    Load an .mp4 fully into memory as pygame surfaces.
    """
    p = os.path.expanduser(os.path.expandvars(str(path)))
    if not os.path.splitext(p)[1]:
        p += ".mp4"

    p = os.path.normpath(p)
    root_abs = os.path.normpath(os.path.abspath(exp_root))
    p_abs = os.path.normpath(os.path.abspath(p))

    if not os.path.isabs(p):
        try:
            if os.path.commonpath([p_abs, root_abs]) != root_abs:
                p = os.path.join(exp_root, p)
        except ValueError:
            p = os.path.join(exp_root, p)

    p = os.path.normpath(p)

    if not os.path.exists(p):
        raise FileNotFoundError(f"video not found: {p}")

    cap = cv2.VideoCapture(p)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {p}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    fcnt = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    dur = fcnt / fps if fcnt > 0 else 0

    frames = []
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        surf = pygame.surfarray.make_surface(frame.swapaxes(0, 1))
        frames.append(surf)

    cap.release()

    if not frames:
        raise RuntimeError(f"video contains no readable frames: {p}")

    return {
        "frames": frames,
        "fps": fps,
        "ms_per": 1000.0 / fps,
        "duration": dur,
        "size": frames[0].get_size(),
        "path": p,
    }

_video_buffer_thread = None
_video_buffer_stop = None

def _resolve_video_path(path):
    p = os.path.expanduser(os.path.expandvars(str(path)))
    if not os.path.splitext(p)[1]:
        p += ".mp4"

    p = os.path.normpath(p)
    root_abs = os.path.normpath(os.path.abspath(exp_root))
    p_abs = os.path.normpath(os.path.abspath(p))

    if not os.path.isabs(p):
        try:
            if os.path.commonpath([p_abs, root_abs]) != root_abs:
                p = os.path.join(exp_root, p)
        except ValueError:
            p = os.path.join(exp_root, p)

    p = os.path.normpath(p)

    if not os.path.exists(p):
        raise FileNotFoundError(f"video not found: {p}")

    return p

def video_start_buffered(
    video_spec,
    *,
    rrect=None,
    pos=(0.5, 0.5),
    keep_aspect=True,
    loop=False
):
    """
    Begin playing a video stimulus from disk with a small prefetch queue.
    """
    global _video_state, _video_buffer_thread, _video_buffer_stop

    video_stop()

    p = _resolve_video_path(video_spec["path"])
    prefetch_n = int(video_spec.get("prefetch_n", 8))

    cap = cv2.VideoCapture(p)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {p}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    fcnt = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    dur = fcnt / fps if fcnt > 0 else 0

    ret, frame = cap.read()
    if not ret:
        cap.release()
        raise RuntimeError(f"video contains no readable frames: {p}")

    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    first_surf = pygame.surfarray.make_surface(frame.swapaxes(0, 1))
    vw, vh = first_surf.get_size()

    sw, sh = screen.get_size()

    if rrect is not None:
        rcx, rcy, rw, rh = rrect
        tgt_w = int(rw * sw)
        tgt_h = int(rh * sh)

        if keep_aspect:
            scale = min(tgt_w / vw, tgt_h / vh)
            dw, dh = int(vw * scale), int(vh * scale)
        else:
            dw, dh = tgt_w, tgt_h

        cx, cy = int(rcx * sw), int(rcy * sh)
    else:
        rcx, rcy = pos
        dw, dh = vw, vh
        cx, cy = int(rcx * sw), int(rcy * sh)

    dest = pygame.Rect(0, 0, dw, dh)
    dest.center = (cx, cy)

    stop_evt = threading.Event()
    queue_lock = threading.Lock()
    frame_queue = deque([first_surf])

    _video_buffer_stop = stop_evt

    _video_state = {
        "mode": "buffered",
        "frame_queue": frame_queue,
        "queue_lock": queue_lock,
        "prefetch_n": prefetch_n,
        "fps": fps,
        "ms_per": 1000.0 / fps,
        "next_ms": pygame.time.get_ticks(),
        "dest": dest,
        "loop": loop,
        "keep_aspect": keep_aspect,
        "duration": dur,
        "needs_runtime_scale": ((dw, dh) != (vw, vh)),
        "path": p,
        "cap": cap,
        "eof": False,
    }

    def reader_loop():
        global _video_state
        try:
            while not stop_evt.is_set():
                if _video_state is None:
                    break
                if _video_state.get("cap") is None:
                    break

                with queue_lock:
                    qlen = len(frame_queue)

                if qlen >= prefetch_n:
                    time.sleep(0.005)
                    continue

                ret, frame = cap.read()
                if not ret:
                    _video_state["eof"] = True
                    break

                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                surf = pygame.surfarray.make_surface(frame.swapaxes(0, 1))

                with queue_lock:
                    frame_queue.append(surf)

        finally:
            try:
                cap.release()
            except Exception:
                pass
            if _video_state is not None:
                _video_state["cap"] = None

    _video_buffer_thread = threading.Thread(
        target = reader_loop,
        name = "video-buffer",
        daemon = True
    )
    _video_buffer_thread.start()

    return dur

def video_start(
    vid,
    *,
    rrect=None,                    # (rcx, rcy, rw, rh) – relative center + relative size
    pos=(0.5, 0.5),               # (rcx, rcy) – relative center, original video size
    keep_aspect=True,
    loop=False,
    rescale_precompute=False
):
    """
    Begin playing a preloaded video stimulus without blocking.

    placement modes:
        - if rrect is given: use its center and relative width/height
        - otherwise: use pos and the original video size

    Returns the clip duration in seconds (float).
    Call `video_tick()` from your main loop to advance and draw frames.
    Use `video_is_playing()` to poll its state.
    """
    global _video_state

    frames = vid["frames"]
    if not frames:
        raise RuntimeError("video stimulus has no frames")

    sw, sh = screen.get_size()
    vw, vh = vid["size"]

    if rrect is not None:
        rcx, rcy, rw, rh = rrect
        tgt_w = int(rw * sw)
        tgt_h = int(rh * sh)

        if keep_aspect:
            scale = min(tgt_w / vw, tgt_h / vh)
            dw, dh = int(vw * scale), int(vh * scale)
        else:
            dw, dh = tgt_w, tgt_h

        cx, cy = int(rcx * sw), int(rcy * sh)

    else:
        rcx, rcy = pos
        dw, dh = vw, vh
        cx, cy = int(rcx * sw), int(rcy * sh)

    dest = pygame.Rect(0, 0, dw, dh)
    dest.center = (cx, cy)

    if rescale_precompute and (dw, dh) != (vw, vh):
        play_frames = [pygame.transform.smoothscale(f, (dw, dh)) for f in frames]
    else:
        play_frames = frames

    _video_state = {
        "frames": play_frames,
        "frame_idx": 0,
        "frame_count": len(play_frames),
        "fps": vid["fps"],
        "ms_per": vid["ms_per"],
        "next_ms": pygame.time.get_ticks(),
        "dest": dest,
        "loop": loop,
        "keep_aspect": keep_aspect,
        "duration": vid["duration"],
        "needs_runtime_scale": ((dw, dh) != (vw, vh) and not rescale_precompute),
    }
    return vid["duration"]

def play_visual_stimulus(vid, pos = None, rrect = None):
    if preload_video:
        video_start(vid)
    else:
        video_start_buffered(vid)

def video_tick():
    """
    Advance the current video by one frame if it is time and blit it.
    Call this once per iteration of your main loop before the display flip.
    """
    global _video_state, stimulus_start
    if _video_state is None:
        return

    now = pygame.time.get_ticks()
    if now < _video_state["next_ms"]:
        return

    if _video_state.get("mode") == "buffered":
        with _video_state["queue_lock"]:
            if len(_video_state["frame_queue"]) > 0:
                surf = _video_state["frame_queue"].popleft()
            else:
                surf = None

        if surf is None:
            if _video_state.get("eof", False):
                video_stop()
            return

        _video_state["next_ms"] += _video_state["ms_per"]

        if _video_state["needs_runtime_scale"]:
            surf = pygame.transform.smoothscale(surf, _video_state["dest"].size)

        if stimulus_start is None:
            stimulus_start = exp_time()

        screen.blit(surf, _video_state["dest"])
        return

    idx = _video_state["frame_idx"]

    if idx >= _video_state["frame_count"]:
        if _video_state["loop"]:
            _video_state["frame_idx"] = 0
            idx = 0
        else:
            video_stop()
            return

    surf = _video_state["frames"][idx]
    _video_state["next_ms"] += _video_state["ms_per"]
    _video_state["frame_idx"] += 1

    if _video_state["needs_runtime_scale"]:
        surf = pygame.transform.smoothscale(surf, _video_state["dest"].size)

    if stimulus_start is None:
        stimulus_start = exp_time()
    screen.blit(surf, _video_state["dest"])

def video_is_playing():
    """Return True while a clip started with `video_start` is still active."""
    return _video_state is not None


def video_stop():
    """Abort the current clip and free playback state."""
    global _video_state, _video_buffer_thread, _video_buffer_stop

    if _video_buffer_stop is not None:
        _video_buffer_stop.set()

    if _video_buffer_thread is not None and _video_buffer_thread.is_alive():
        _video_buffer_thread.join(timeout = 1.0)

    if _video_state is not None:
        try:
            cap = _video_state.get("cap")
            if cap is not None:
                cap.release()
        except Exception:
            pass

    _video_state = None
    _video_buffer_thread = None
    _video_buffer_stop = None

#endregion

#region Audio startup
if on_grid:
    audio_settings = settings
    audio_start()
    #DEV: placeholders follow
    #audio_sample  = load_mono_wav(r"Y:\Beno\hangfalfal\sounds\Linda\tri_channel_output_1.wav", to_sample_rate=audio_settings["sample_rate"])[: int(5 * audio_settings["sample_rate"])]
    '''audio_sample  = load_wav(r"Y:\Beno\hangfalfal\sounds\Linda\tri_channel_output_1.wav", to_sample_rate=audio_settings["sample_rate"])
    sp = 33 #DEV: placeholder for now
    ch = audio_settings['sp_to_ch'][sp-1] #DEV: placeholder for now
    speaker_gain = audio_settings['sp_gains'][sp-1] #DEV: placeholder for now'''

    """
    mic_blocksize = 128 #DEV: maybe this is a bit too high res
    mic_meter_start(blocksize=mic_blocksize, channels=1)   # non-blocking
    mic_measurement_gap = mic_blocksize / settings["sample_rate"]

    def mic_level():
        block, sr = mic_raw_block()
        channel0 = block[:, 0]
        level = float(np.sqrt( np.mean( channel0 * channel0 ) ))
        return level
    """
    
    def play_auditory_stimulus(speaker_routing, samp, start_in_s):
        global stimulus_start, master_gain_exp
        if preload_audio:
            routing_spec = {}
            for i in range(len(speaker_routing)):
                sp = speaker_routing[i]
                ch = audio_settings['sp_to_ch'][sp - 1] # Zero indexed
                sp_gain = audio_settings['sp_gains'][sp] # Not zero indexed...
                #print(f"Routing to speaker {sp} through channel {ch} with gain {sp_gain}")
                routing_spec[ch] = (
                    samp[i] * sp_gain * master_gain_exp,
                    start_in_s
                )

            channels_play_at(routing_spec)
            stimulus_start = exp_time()
        else:
            play_auditory_stimulus_buffered(speaker_routing,samp,start_in_s)
else:  
    audio_settings = settings
    audio_settings["device_id"] = get_default_output_device_id()
    audio_start(device_id=audio_settings["device_id"], ch_num=2)
    '''audio_sample  = load_wav(f'{exp_root}/inputs/stimuli/tri.wav', to_sample_rate=audio_settings["sample_rate"])
    ch = None
    speaker_gain = 1'''
    def play_auditory_stimulus(speaker_routing, samp, start_in_s):
        global stimulus_start
        if preload_audio:
            play_wav_lr(samp * master_gain_exp)
            stimulus_start = exp_time()
        else:
            play_auditory_stimulus_buffered(speaker_routing,samp,start_in_s)

_audio_buffer_thread = None
_audio_buffer_stop = None

def stop_buffered_audio():
    global _audio_buffer_thread, _audio_buffer_stop

    if _audio_buffer_stop is not None:
        _audio_buffer_stop.set()

    if _audio_buffer_thread is not None and _audio_buffer_thread.is_alive():
        _audio_buffer_thread.join(timeout = 1.0)

    _audio_buffer_thread = None
    _audio_buffer_stop = None

def _resample_chunk_if_needed(frames, sr_in, sr_out):
    if sr_in == sr_out:
        return frames.astype(np.float32, copy = False)

    chans = []
    for k in range(frames.shape[1]):
        chans.append(cb._resample_to(frames[:, k], sr_in, sr_out))

    if len(chans) == 0:
        return np.zeros((0, 0), dtype = np.float32)

    n_min = min(len(ch) for ch in chans)
    if n_min == 0:
        return np.zeros((0, frames.shape[1]), dtype = np.float32)

    return np.column_stack([ch[:n_min] for ch in chans]).astype(np.float32, copy = False)

def _buffered_audio_backlog_s(target_channels):
    with cb._lock:
        if cb._chan_bufs is None:
            return 0.0
        if len(target_channels) == 0:
            return 0.0
        available = []
        for ch in target_channels:
            if ch < 0:
                continue
            available.append(cb._chan_bufs[ch].size)
        if len(available) == 0:
            return 0.0
        return min(available) / float(audio_settings["sample_rate"])

def play_auditory_stimulus_buffered(speaker_routing, buffered_spec, start_in_s):
    global stimulus_start, master_gain_exp, _audio_buffer_thread, _audio_buffer_stop

    import soundfile as sf

    stop_buffered_audio()

    path = buffered_spec['path']
    chunk_s = buffered_spec.get('chunk_s', 0.35)
    prefill_s = buffered_spec.get('prefill_s', 1.0)
    topup_s = buffered_spec.get('topup_s', 0.5)

    stop_evt = threading.Event()
    _audio_buffer_stop = stop_evt

    if on_grid:
        route_pairs = []
        for i in range(len(speaker_routing)):
            sp = speaker_routing[i]
            ch = audio_settings['sp_to_ch'][sp - 1] # Zero indexed
            sp_gain = audio_settings['sp_gains'][sp] # Not zero indexed...
            if ch >= 0:
                route_pairs.append((i, ch, sp_gain * master_gain_exp))
        target_channels = [pair[1] for pair in route_pairs]
    else:
        route_pairs = None
        target_channels = [0, 1]

    with sf.SoundFile(path, 'r') as f:
        sr_in = int(f.samplerate)
        chunk_frames_in = max(1, int(round(chunk_s * sr_in)))

        first_chunk = f.read(chunk_frames_in, dtype = 'float32', always_2d = True)

    if first_chunk.size == 0:
        return

    first_chunk = _resample_chunk_if_needed(first_chunk, sr_in, audio_settings["sample_rate"])

    if first_chunk.size == 0:
        return

    if on_grid:
        routing_spec = {}
        for wav_ch_i, out_ch, gain in route_pairs:
            if wav_ch_i >= first_chunk.shape[1]:
                continue
            routing_spec[out_ch] = (
                first_chunk[:, wav_ch_i] * gain,
                start_in_s
            )
        if len(routing_spec) == 0:
            return
        channels_play_at(routing_spec)
    else:
        play_wav_lr(first_chunk * master_gain_exp, start_in_s = start_in_s)
    
    stimulus_start = exp_time()

    def run_buffer():
        try:
            with sf.SoundFile(path, 'r') as f:
                f.seek(chunk_frames_in)

                while not stop_evt.is_set():
                    backlog_s = _buffered_audio_backlog_s(target_channels)

                    if backlog_s >= prefill_s:
                        break

                    frames = f.read(chunk_frames_in, dtype = 'float32', always_2d = True)
                    if frames.size == 0:
                        break

                    frames = _resample_chunk_if_needed(frames, sr_in, audio_settings["sample_rate"])
                    if frames.size == 0:
                        break

                    if on_grid:
                        for wav_ch_i, out_ch, gain in route_pairs:
                            if wav_ch_i >= frames.shape[1]:
                                continue
                            channel_append(out_ch, frames[:, wav_ch_i] * gain)
                    else:
                        left = frames[:, 0]
                        right = frames[:, 1] if frames.shape[1] >= 2 else frames[:, 0]
                        channel_append(0, left * master_gain_exp)
                        channel_append(1, right * master_gain_exp)

                while not stop_evt.is_set():
                    backlog_s = _buffered_audio_backlog_s(target_channels)

                    if backlog_s <= topup_s:
                        frames = f.read(chunk_frames_in, dtype = 'float32', always_2d = True)

                        if frames.size == 0:
                            break

                        frames = _resample_chunk_if_needed(frames, sr_in, audio_settings["sample_rate"])
                        if frames.size == 0:
                            break

                        if on_grid:
                            for wav_ch_i, out_ch, gain in route_pairs:
                                if wav_ch_i >= frames.shape[1]:
                                    continue
                                channel_append(out_ch, frames[:, wav_ch_i] * gain)
                        else:
                            left = frames[:, 0]
                            right = frames[:, 1] if frames.shape[1] >= 2 else frames[:, 0]
                            channel_append(0, left * master_gain_exp)
                            channel_append(1, right * master_gain_exp)
                    else:
                        time.sleep(0.01)

        except Exception as e:
            print(f"Buffered audio playback failed for {path}: {e}")

    _audio_buffer_thread = threading.Thread(
        target = run_buffer,
        name = "audio-buffer",
        daemon = True
    )
    _audio_buffer_thread.start()
#endregion

#endregion

#region Data IO

#region Load instructions and stimuli
instructions = {}
for stage in exp_structure:
    try:
        with open(f'{exp_root}/inputs/instructions/{stage["name"]}.txt', 'r') as file:
            instructions[stage['name']] = file.read().splitlines()
    except FileNotFoundError:
        continue

preload_audio = False
preload_video = False
stimuli = {}
routing = {}
print(f"{'Loading' if preload_audio or preload_video else 'Looking up'} stimuli and {'' if preload_audio or preload_video else 'loading'} routing, please wait.")

if demo:
    draw_loading_screen()
    if preload_audio:
        stimuli['demo_aud'] = load_wav(
            f"{exp_root}/inputs/stimuli/AUD_demo.wav",
            to_sample_rate = audio_settings["sample_rate"]
        )
    else:
        stimuli['demo_aud'] = {
            'path': f"{exp_root}/inputs/stimuli/AUD_demo.wav",
            'chunk_s': 0.35,
            'prefill_s': 1.0,
            'topup_s': 0.5
        }

    with open(f"{exp_root}/inputs/stimuli/AUD_demo.csv", newline="", encoding="utf-8") as file:
        routing['demo_aud'] = [int(x) for x in next(csv.reader(file))]

    draw_loading_screen()
    if preload_video:
        stimuli['demo_vis'] = load_mp4(f"{exp_root}/inputs/stimuli/VIS_demo.mp4")
    else:
        stimuli['demo_vis'] = {
            'path': f"{exp_root}/inputs/stimuli/VIS_demo.mp4",
            'prefetch_n': 8
        }

else:
    for stage in exp_structure:
        draw_loading_screen()
        if 'modifiers' in stage:
            try:
                if stage['type'] == 'test':
                    if preload_audio or preload_video:
                        stimuli[stage['name']] = {}
                    else:
                        stimuli[stage['name']] = {}

                    routing[stage['name']] = {}

                    for condition_name in test_conditions[f"{stage['modifiers']['phase']}_{stage['modifiers']['modality']}"]:
                        if stage['modifiers']['modality'] == 'aud':
                            load_name = f"{stage['modifiers']['phase']}_test{condition_name}_{stage['modifiers']['modality']}_sID11{stimulus_set}"

                            if preload_audio:
                                stimuli[stage['name']][condition_name] = load_wav(
                                    f"{exp_root}/inputs/stimuli/{load_name}.wav",
                                    to_sample_rate = audio_settings["sample_rate"]
                                )
                            else:
                                stimuli[stage['name']][condition_name] = {
                                    'path': f"{exp_root}/inputs/stimuli/{load_name}.wav",
                                    'chunk_s': 0.35,
                                    'prefill_s': 1.0,
                                    'topup_s': 0.5
                                }

                            with open(f"{exp_root}/inputs/stimuli/{load_name}.csv", newline="", encoding="utf-8") as file:
                                routing[stage['name']] = [int(x) for x in next(csv.reader(file))]

                        else:
                            load_name = f"{stage['modifiers']['phase']}_test{condition_name}_{stage['modifiers']['modality']}_sID11{stimulus_set}.mp4"

                            if preload_video:
                                stimuli[stage['name']][condition_name] = load_mp4(
                                    f"{exp_root}/inputs/stimuli/{load_name}"
                                )
                            else:
                                stimuli[stage['name']][condition_name] = {
                                    'path': f"{exp_root}/inputs/stimuli/{load_name}",
                                    'prefetch_n': 8
                                }

                else:
                    if stage['modifiers']['modality'] == 'aud':
                        if preload_audio:
                            stimuli[stage['name']] = load_wav(
                                f"{exp_root}/inputs/stimuli/{stage['name']}.wav",
                                to_sample_rate = audio_settings["sample_rate"]
                            )
                        else:
                            stimuli[stage['name']] = {
                                'path': f"{exp_root}/inputs/stimuli/{stage['name']}.wav",
                                'chunk_s': 0.35,
                                'prefill_s': 1.0,
                                'topup_s': 0.5
                            }

                        with open(f"{exp_root}/inputs/stimuli/{stage['name']}.csv", newline="", encoding="utf-8") as file:
                            routing[stage['name']] = [int(x) for x in next(csv.reader(file))]

                    else:
                        if preload_video:
                            stimuli[stage['name']] = load_mp4(f"{exp_root}/inputs/stimuli/{stage['name']}.mp4")
                        else:
                            stimuli[stage['name']] = {
                                'path': f"{exp_root}/inputs/stimuli/{stage['name']}.mp4",
                                'prefetch_n': 8
                            }

            except FileNotFoundError:
                continue

exp_settings_and_data['speaker_routing'] = routing
#endregion

#region Save
data_to_save = [ 
    # Add dicts or arrays here that needs saving as separate files.
    # Dicts gets exported to json, 1D list to txt, 2D table to csv.
    # Include a value with key 'header' if a 2D table.
    {"content":exp_settings_and_data,"name":"exp_settings_and_data"},
    {"content":subject_data,"name":"subject_data"}
]

subject_data['space_presses'] = {}
subject_data['repeat_num'] = {}
#endregion

#region GitHub
def git_commit_and_sync_from_root(
    target_from_root=".",
    message=None,
    *,
    anchor_file=__file__,
    pull_rebase=True,
):
    """
    Commit + sync changes for `target_from_root` (interpreted relative to the repo root),
    locating the repo root from `anchor_file` (a file guaranteed to live inside the repo).

    Works regardless of current working directory.

    Returns: dict(repo_root=..., did_commit=..., outputs=[(label, stdout), ...])
    Raises RuntimeError on git errors.
    """
    def run_git(args, cwd):
        try:
            p = subprocess.run(
                ["git", "-C", cwd, *args],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as e:
            raise RuntimeError("git executable not found on PATH") from e

        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        if p.returncode != 0:
            msg = f"git failed: git -C {cwd} {' '.join(args)}"
            if out:
                msg += f"\nstdout:\n{out}"
            if err:
                msg += f"\nstderr:\n{err}"
            raise RuntimeError(msg)
        return out

    anchor_dir = os.path.dirname(os.path.abspath(anchor_file))
    repo_root = run_git(["rev-parse", "--show-toplevel"], anchor_dir)

    # interpret target as repo-root-relative, unless it's absolute (then convert)
    if os.path.isabs(target_from_root):
        ap = os.path.normpath(target_from_root)
        rr = os.path.normpath(repo_root)
        if os.path.commonpath([ap, rr]) != rr:
            raise RuntimeError(f"target_from_root is not inside this repo: {target_from_root}")
        rel = os.path.relpath(ap, repo_root)
    else:
        rel = os.path.normpath(target_from_root) if target_from_root not in (".", "", None) else "."

    # git likes forward slashes in pathspecs on windows too
    rel_git = rel.replace("\\", "/") if rel != "." else "."

    outputs = []

    # sync remote -> local first
    if pull_rebase:
        outputs.append(("pull --rebase --autostash", run_git(["pull", "--rebase", "--autostash"], repo_root)))
    else:
        outputs.append(("pull --ff-only", run_git(["pull", "--ff-only"], repo_root)))

    # stage changes (incl deletions) for just the target path
    outputs.append(("add -A", run_git(["add", "-A", "--", rel_git], repo_root)))

    # commit only if something is staged
    did_commit = False
    diff_rc = subprocess.run(["git", "-C", repo_root, "diff", "--cached", "--quiet"]).returncode
    if diff_rc == 1:
        if message is None:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = f"sync {rel_git} ({ts})"
        outputs.append(("commit", run_git(["commit", "-m", message], repo_root)))
        did_commit = True
    elif diff_rc != 0:
        raise RuntimeError("unexpected error while checking staged diff")

    # push
    outputs.append(("push", run_git(["push"], repo_root)))

    return {"repo_root": repo_root, "did_commit": did_commit, "outputs": outputs}
#endregion

#endregion

#endregion

#region Utils

#region Colors
BLACK   = (0,   0,   0)
WHITE   = (255, 255, 255)
MIDDLEGRAY    = (128, 128, 128)
LIGHTGRAY    = (220, 220, 220)
RED     = (255, 0,   0)
GREEN   = (0,   255, 0)
BLUE    = (0,   0,   255)
YELLOW  = (255, 255, 0)
CYAN    = (0,   255, 255)
MAGENTA = (255, 0,   255)
ORANGE  = (255, 165, 0)
PURPLE  = (128, 0,   128)
BROWN   = (165, 42,  42)
#endregion

#region UI
'''
Text display, buttons, etc.
'''
def text_on_screen(
    text_or_lines,
    rx=0.5, ry=0.5,
    size=font_size,
    color=(0, 0, 0),
    font_path=None,
    antialias=True,
    line_gap=6,
    bg=None,
    bg_pad=6,
    bg_alpha=None,
    align="center",   # "center" | "left" | "right"
):
    if isinstance(text_or_lines, (list, tuple)):
        lines = list(text_or_lines)
    else:
        lines = str(text_or_lines).splitlines()

    if not font_path:
        font = ui_font
    else:
        font = pygame.font.Font(font_path, size)

    sw, sh = screen.get_size()
    cx = int(rx * sw)
    cy = int(ry * sh)

    surfs = [font.render(line, antialias, color) for line in lines]
    total_h = sum(s.get_height() for s in surfs) + line_gap * (len(surfs) - 1)
    y = cy - total_h // 2

    for s in surfs:
        if align == "left":
            rect = s.get_rect(topleft=(cx, y))
        elif align == "right":
            rect = s.get_rect(topright=(cx, y))
        else:  # "center"
            rect = s.get_rect(midtop=(cx, y))

        if bg is not None:
            bg_rect = rect.inflate(bg_pad * 2, bg_pad * 2)
            if bg_alpha is None:
                pygame.draw.rect(screen, bg, bg_rect)
            else:
                tmp = pygame.Surface(bg_rect.size, pygame.SRCALPHA)
                tmp.fill((bg[0], bg[1], bg[2], bg_alpha*255))
                screen.blit(tmp, bg_rect.topleft)

        screen.blit(s, rect)
        y += s.get_height() + line_gap

def wrapped_text_on_screen(
    text_or_lines,
    box_rrect,                    # (rcx, rcy, rw, rh) – centre-anchored, in 0..1 units
    *,
    size       = font_size,
    color      = (0, 0, 0),
    font_path  = None,
    antialias  = True,
    line_gap   = 6,
    bg         = None,
    bg_pad     = 6,
    bg_alpha   = None,
    align      = "center",        # "center" | "left" | "right"
):
    """
    A superset of `text_on_screen`:

    • Accepts either a string **or** a list/tuple of already-split lines.
      If you give a list/tuple, *each* element is treated as its own paragraph
      and word-wrapped separately.
    • Ensures no rendered line exceeds the width of `box_rrect`.
    • Vertically centres the wrapped block inside `box_rrect`.
    """

    # ------------------------------------------------------------------ set-up
    if not font_path:
        font = ui_font
    else:
        font = pygame.font.Font(font_path, size)

    sw, sh = screen.get_size()
    rcx, rcy, rw, rh = box_rrect
    box_w = int(rw * sw)
    box_h = int(rh * sh)

    # ------------------------------------------------------------------ prepare raw paragraphs
    if isinstance(text_or_lines, (list, tuple)):
        raw_paragraphs = [str(p) for p in text_or_lines]
    else:
        raw_paragraphs = str(text_or_lines).splitlines()

    # ------------------------------------------------------------------ word-wrap each paragraph
    space_w, _ = font.size(" ")
    wrapped_lines = []

    for para in raw_paragraphs:
        words = para.split()
        cur_line, cur_w = [], 0

        for w in words or [""]:          # keep empty lines
            w_w, _ = font.size(w)
            if cur_line and cur_w + space_w + w_w > box_w:
                wrapped_lines.append(" ".join(cur_line))
                cur_line, cur_w = [w], w_w
            else:
                if cur_line:
                    cur_w += space_w
                cur_line.append(w)
                cur_w += w_w
        wrapped_lines.append(" ".join(cur_line))

    # ------------------------------------------------------------------ vertical centring inside box
    sample_h = font.size("Tg")[1]
    total_h  = len(wrapped_lines) * sample_h + (len(wrapped_lines) - 1) * line_gap
    first_line_top = rcy * sh - box_h / 2 + max(0, (box_h - total_h) / 2)

    # ------------------------------------------------------------------ delegate drawing line-by-line
    y = int(first_line_top)
    cx = int(rcx * sw)

    for line in wrapped_lines:
        surf = font.render(line, antialias, color)
        if align == "left":
            rect = surf.get_rect(topleft=(cx, y))
        elif align == "right":
            rect = surf.get_rect(topright=(cx, y))
        else:  # center
            rect = surf.get_rect(midtop=(cx, y))

        if bg is not None:
            bg_rect = rect.inflate(bg_pad * 2, bg_pad * 2)
            if bg_alpha is None:
                pygame.draw.rect(screen, bg, bg_rect)
            else:
                tmp = pygame.Surface(bg_rect.size, pygame.SRCALPHA)
                tmp.fill((*bg, int(bg_alpha * 255)))
                screen.blit(tmp, bg_rect.topleft)

        screen.blit(surf, rect)
        y += surf.get_height() + line_gap
ui = [] # global ui registry

class TextField:
    focused_field = None  # track the one global focused field

    def __init__(
        self,
        registry,
        rrect,
        text="",
        placeholder="",
        on_submit=None,
        max_len=None,
        header=None,            # optional header text (centered above the box)
    ):
        self.rrect = rrect              # (rcx, rcy, rw, rh) center-anchored, all [0..1]
        self.rect = pygame.Rect(0, 0, 0, 0)

        self.text = str(text)
        self.placeholder = str(placeholder)
        self.header = header

        self.on_submit = on_submit
        self.max_len = max_len

        self.active = False
        self.focused = False

        # cursor + backspace repeat
        self.cursor = len(self.text)    # insertion point, 0..len(text)
        self._bs_down = False
        self._bs_delay = 0.35           # seconds
        self._bs_interval = 0.05        # seconds
        self._bs_next = 0.0

        self.relayout()
        registry.append(self)

    def relayout(self):
        sw, sh = screen.get_size()
        rcx, rcy, rw, rh = self.rrect
        w = int(rw * sw)
        h = int(rh * sh)
        cx = int(rcx * sw)
        cy = int(rcy * sh)

        self.rect = pygame.Rect(0, 0, w, h)
        self.rect.center = (cx, cy)

        # keep IME rect consistent after resize if focused
        if self.focused:
            _, box_rect = self._header_and_box_rect(ui_font)
            pygame.key.set_text_input_rect(box_rect)

    def show(self):
        self.active = True

    def hide(self):
        self.active = False
        self.blur()

    def focus(self):
        # blur any previously focused field
        if TextField.focused_field is not None and TextField.focused_field is not self:
            TextField.focused_field.blur()

        TextField.focused_field = self
        self.focused = True

        self.cursor = min(max(0, self.cursor), len(self.text))

        pygame.key.start_text_input()
        _, box_rect = self._header_and_box_rect(ui_font)
        pygame.key.set_text_input_rect(box_rect)

    def blur(self):
        if not self.focused:
            return

        self.focused = False
        self._bs_down = False

        if TextField.focused_field is self:
            TextField.focused_field = None

        pygame.key.stop_text_input()

    def _header_and_box_rect(self, font):
        if not self.header:
            return None, self.rect

        header_h = int(font.get_linesize() + 8 * zoom)
        header_h = min(header_h, self.rect.h)

        header_rect = pygame.Rect(self.rect.left, self.rect.top, self.rect.w, header_h)

        box_rect = self.rect.copy()
        box_rect.top = header_rect.bottom
        box_rect.h = max(1, self.rect.bottom - box_rect.top)

        return header_rect, box_rect

    def _insert_at_cursor(self, s):
        if not s:
            return
        if self.max_len is not None and len(self.text) >= self.max_len:
            return

        if self.max_len is not None:
            s = s[: max(0, self.max_len - len(self.text))]

        c = max(0, min(self.cursor, len(self.text)))
        self.text = self.text[:c] + s + self.text[c:]
        self.cursor = c + len(s)

    def _backspace_once(self):
        if self.cursor <= 0:
            return
        c = min(self.cursor, len(self.text))
        self.text = self.text[:c - 1] + self.text[c:]
        self.cursor = c - 1

    def update(self):
        # held-backspace repeat (safe to call every frame)
        if not (self.active and self.focused):
            return
        if not self._bs_down:
            return

        now = exp_time()
        if now < self._bs_next:
            return

        while self._bs_down and now >= self._bs_next:
            if self.cursor <= 0:
                self._bs_down = False
                break
            self._backspace_once()
            self._bs_next += self._bs_interval

    def _wrap_para(self, s, font, max_w, base):
        # word-wrap one paragraph (no '\n' inside). keeps words unbroken.
        # returns list of {start,end,text} in *global* indices.
        if s == "":
            return [{"start": base, "end": base, "text": ""}]

        segs = []
        i = 0
        n = len(s)

        while i < n:
            start = i
            last_space = -1
            j = i

            while j < n and font.size(s[start:j + 1])[0] <= max_w:
                if s[j] == " ":
                    last_space = j
                j += 1

            if j == n:
                end = n
                segs.append({"start": base + start, "end": base + end, "text": s[start:end]})
                i = n
            else:
                if last_space >= start:
                    end = last_space
                    segs.append({"start": base + start, "end": base + end, "text": s[start:end]})
                    k = last_space
                    while k < n and s[k] == " ":
                        k += 1
                    i = k
                else:
                    # first word longer than max_w -> keep unbroken (it will be clipped)
                    k = start
                    while k < n and s[k] != " ":
                        k += 1
                    end = k
                    segs.append({"start": base + start, "end": base + end, "text": s[start:end]})
                    while k < n and s[k] == " ":
                        k += 1
                    i = k

        return segs if segs else [{"start": base, "end": base, "text": ""}]

    def _layout_lines(self, text, font, max_w):
        # returns wrapped visual lines with global index ranges
        # newline chars are not included in line ranges
        lines = []
        pos = 0
        L = len(text)

        while True:
            nl = text.find("\n", pos)
            if nl == -1:
                para_end = L
                has_nl = False
            else:
                para_end = nl
                has_nl = True

            para = text[pos:para_end]
            lines.extend(self._wrap_para(para, font, max_w, base=pos))

            if not has_nl:
                break

            pos = nl + 1
            if pos > L:
                break
            if pos == L:
                # trailing newline => final empty line
                lines.append({"start": pos, "end": pos, "text": ""})
                break

        return lines if lines else [{"start": 0, "end": 0, "text": ""}]

    def _cursor_line_index(self, cursor, lines):
        if not lines:
            return 0
        cursor = max(0, min(cursor, len(self.text)))

        if cursor <= lines[0]["start"]:
            return 0

        for i, seg in enumerate(lines):
            s, e = seg["start"], seg["end"]
            if s <= cursor <= e:
                return i
            if i < len(lines) - 1:
                ns = lines[i + 1]["start"]
                if e < cursor < ns:
                    return i

        return len(lines) - 1

    def handle_event(self, event):
        if not self.active:
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.focus()
                # simple policy: clicking focuses and puts cursor at end
                self.cursor = len(self.text)
            else:
                if self.focused:
                    self.blur()
            return

        if not self.focused:
            return

        if event.type == pygame.TEXTINPUT:
            self._insert_at_cursor(event.text)

        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_BACKSPACE:
                self._backspace_once()
                now = exp_time()
                self._bs_down = True
                self._bs_next = now + self._bs_delay

            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._insert_at_cursor("\n")

            elif event.key == pygame.K_LEFT:
                self.cursor = max(0, self.cursor - 1)

            elif event.key == pygame.K_RIGHT:
                self.cursor = min(len(self.text), self.cursor + 1)

            elif event.key == pygame.K_ESCAPE:
                self.blur()

        elif event.type == pygame.KEYUP:
            if event.key == pygame.K_BACKSPACE:
                self._bs_down = False

    def get(self):
        return self.text

    def submit(self):
        if self.on_submit:
            self.on_submit(self.get())

    def draw(self):
        if not self.active:
            return

        self.update()  # enables held-backspace without main-loop changes

        font = ui_font
        header_rect, box_rect = self._header_and_box_rect(font)

        # header (centered)
        if self.header and header_rect:
            label = self.header
            while label and font.size(label)[0] > header_rect.w:
                label = label[:-1]
            hs = font.render(label, True, BLACK)
            hx = header_rect.centerx - hs.get_width() // 2
            hy = header_rect.centery - hs.get_height() // 2
            screen.blit(hs, (hx, hy))

        # box: white fill + black outline (only the text box area)
        pygame.draw.rect(screen, WHITE, box_rect)
        pygame.draw.rect(screen, BLACK, box_rect, 1)

        pad = int(10 * zoom)
        inner = box_rect.inflate(-2 * pad, -2 * pad)

        showing_placeholder = (not self.text and not self.focused)
        show_text = self.placeholder if showing_placeholder else self.text
        show_color = MIDDLEGRAY if showing_placeholder else BLACK

        line_h = max(1, font.get_linesize())
        max_lines = max(1, inner.h // line_h)

        lines = self._layout_lines(show_text, font, inner.w)

        # hide overflow on top; when focused keep cursor line visible
        if self.focused and not showing_placeholder:
            cur_idx = self._cursor_line_index(self.cursor, lines)
            first = max(0, cur_idx - max_lines + 1)
        else:
            first = max(0, len(lines) - max_lines)

        last = min(len(lines), first + max_lines)
        visible = lines[first:last]

        prev_clip = screen.get_clip()
        screen.set_clip(inner)

        y = inner.top
        for seg in visible:
            surf = font.render(seg["text"], True, show_color)
            screen.blit(surf, (inner.left, y))
            y += line_h

        screen.set_clip(prev_clip)

        # caret (blink) at cursor
        if self.focused and (not showing_placeholder) and (int(exp_time() * 2) % 2 == 0):
            cur_idx = self._cursor_line_index(self.cursor, lines)

            if first <= cur_idx < last:
                seg = lines[cur_idx]
                seg_start, seg_end = seg["start"], seg["end"]
                local_cursor = max(seg_start, min(self.cursor, seg_end))

                prefix = self.text[seg_start:local_cursor]
                caret_x = inner.left + font.size(prefix)[0] + 1

                row = cur_idx - first
                caret_y = inner.top + row * line_h
                caret_y0 = caret_y + 1
                caret_y1 = caret_y + line_h - 2

                caret_x = min(max(caret_x, inner.left + 1), inner.right - 2)
                caret_y0 = max(caret_y0, inner.top + 1)
                caret_y1 = min(caret_y1, inner.bottom - 2)

                caret_w = max(1, int(line_h * 0.10))
                pygame.draw.line(screen, BLACK, (caret_x, caret_y0), (caret_x, caret_y1), caret_w)

class RadioButtons:
    def __init__(
        self,
        registry,
        options,
        *,
        rrect,                 # (cx, cy, w, h) normalized [0..1], center-based
        on_submit=None,
        allow_other=False,
        other_placeholder="",
        layout="vertical",     # "vertical" (default) or "horizontal"
        header=None,           # optional string drawn above options
    ):
        self.rrect = rrect
        self.options = list(options)
        self.on_submit = on_submit
        self.allow_other = allow_other
        self.other_placeholder = other_placeholder
        self.layout = layout
        self.header = header

        self.active = False
        self.selected = None

        self.item_rects = []
        self.header_rect = None

        self.other_idx = None
        self.other_field = None

        if self.allow_other:
            self.other_idx = len(self.options)
            self.other_field = TextField([], (0.5, 0.5, 0.5, 0.5), text="", placeholder=other_placeholder)
            self.other_field.show()

        self.relayout()
        registry.append(self)

    def show(self):
        self.active = True
        if self.other_field:
            self.other_field.show()

    def hide(self):
        self.active = False
        if self.other_field:
            self.other_field.hide()

    def relayout(self):
        sw, sh = screen.get_size()
        font = ui_font

        pad = int(10 * zoom)  # matches TextField pad
        # ensure row height comfortably fits a TextField (prevents "short vertical line" look)
        natural_line_h = max(
            int(font.get_linesize() + 2 * pad + 6 * zoom),
            int(40 * zoom),
        )

        n_items = len(self.options) + (1 if self.allow_other else 0)

        # block bounds from rrect
        bcx, bcy, bw, bh = self.rrect
        cx = int(bcx * sw)
        cy = int(bcy * sh)
        block_w = max(1, int(bw * sw))
        block_h = max(1, int(bh * sh))

        left = cx - block_w // 2
        block_top = cy - block_h // 2
        width = block_w

        # header area
        header_h = 0
        self.header_rect = None
        if self.header:
            header_h = int(font.get_linesize() + 8 * zoom)
            self.header_rect = pygame.Rect(left, block_top, width, header_h)

        avail_top = block_top + header_h
        avail_h = max(1, block_h - header_h)

        circle_r = int(10 * zoom)

        self.item_rects = []

        if self.layout == "horizontal":
            # one row, split width
            line_h = max(1, min(natural_line_h, avail_h))
            total_h = line_h
            top = avail_top + (avail_h - total_h) // 2

            gap = int(12 * zoom)
            total_gap = gap * max(0, n_items - 1)
            item_w = max(1, (width - total_gap) // max(1, n_items))

            x = left
            for i in range(n_items):
                w_i = item_w
                # put leftover pixels into the last item
                if i == n_items - 1:
                    w_i = (left + width) - x
                r = pygame.Rect(x, top, w_i, line_h)
                self.item_rects.append(r)
                x += w_i + gap

        else:
            # vertical (default)
            n_rows = n_items
            line_h = max(1, min(natural_line_h, avail_h // max(1, n_rows)))
            total_h = line_h * n_rows
            top = avail_top + (avail_h - total_h) // 2

            for i in range(n_rows):
                r = pygame.Rect(left, top + i * line_h, width, line_h)
                self.item_rects.append(r)

        # place other textbox inline in its item rect
        if self.other_field and self.other_idx is not None:
            r = self.item_rects[self.other_idx]
            circle_x = r.left + int(20 * zoom)

            field_left = circle_x + int(25 * zoom)
            field_right = r.right - int(10 * zoom)
            field_w = max(10, field_right - field_left)

            # make it tall enough so TextField padding doesn't eat it
            min_h = int(font.get_linesize() + 2 * pad + 4)
            field_h = max(min_h, r.h - int(6 * zoom))
            field_h = min(field_h, r.h)  # do not exceed row height

            field_rect = pygame.Rect(0, 0, field_w, field_h)
            field_rect.midleft = (field_left, r.centery)

            self.other_field.rrect = (
                field_rect.centerx / sw,
                field_rect.centery / sh,
                field_rect.w / sw,
                field_rect.h / sh,
            )
            self.other_field.relayout()

        self._layout_cache = {"circle_r": circle_r, "font": font}

    def handle_event(self, event):
        if not self.active:
            return

        # click directly into textbox -> select other + focus
        if self.other_field and event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.other_field.rect.collidepoint(event.pos):
                self.selected = self.other_idx
                self.other_field.focus()
                self.other_field.handle_event(event)
                return

        # let textbox process typing/backspace etc when present
        if self.other_field:
            self.other_field.handle_event(event)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, r in enumerate(self.item_rects):
                if r.collidepoint(event.pos):
                    self.selected = i
                    if self.other_field:
                        if i == self.other_idx:
                            self.other_field.focus()
                        else:
                            self.other_field.blur()
                    return

    def submit(self):
        if self.on_submit:
            self.on_submit(self.get())

    def get(self):
        if self.selected is None:
            return None
        if self.other_field and self.selected == self.other_idx:
            return self.other_field.get()
        return self.options[self.selected]

    def draw(self):
        if not self.active:
            return

        circle_r = self._layout_cache["circle_r"]
        font = self._layout_cache["font"]

        # header
        if self.header and self.header_rect:
            label = self.header
            # simple right-clip if too wide
            while label and font.size(label)[0] > self.header_rect.w:
                label = label[:-1]
            surf = font.render(label, True, BLACK)
            
            x = self.header_rect.left
            y = self.header_rect.centery - surf.get_height() // 2
            screen.blit(surf, (x, y))

            # Centered header
            #x = self.header_rect.centerx - surf.get_width() // 2
            #y = self.header_rect.centery - surf.get_height() // 2
            #screen.blit(surf, (x, y))

        # items
        for i, r in enumerate(self.item_rects):
            cy = r.centery
            circle_x = r.left + int(20 * zoom)

            pygame.draw.circle(screen, BLACK, (circle_x, cy), circle_r, 2)
            if self.selected == i:
                pygame.draw.circle(screen, BLACK, (circle_x, cy), max(1, circle_r - 4))

            if self.other_field and i == self.other_idx:
                self.other_field.draw()
            else:
                label = self.options[i]
                # clip label to available width
                avail = max(1, r.right - (circle_x + int(25 * zoom)) - int(6 * zoom))
                while label and font.size(label)[0] > avail:
                    label = label[:-1]
                surf = font.render(label, True, BLACK)
                screen.blit(surf, (circle_x + int(25 * zoom), cy - surf.get_height() // 2))

class Button:
    def __init__(self, registry, rrect, text, on_click):
        self.rrect = rrect                # (rcx, rcy, rw, rh) in [0..1] (CENTER-ANCHORED)
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.text = text
        self.on_click = on_click
        self.relayout()

        self.visible = False
        self.enabled = False

        self.hovered = False

        registry.append(self)

    def relayout(self):
        sw, sh = screen.get_size()
        rcx, rcy, rw, rh = self.rrect

        w = int(rw * sw)
        h = int(rh * sh)
        cx = int(rcx * sw)
        cy = int(rcy * sh)

        self.rect = pygame.Rect(0, 0, w, h)
        self.rect.center = (cx, cy)

    def handle_event(self, event):
        if not self.visible:
            return

        if event.type == pygame.MOUSEMOTION:
            self.hovered = self.rect.collidepoint(event.pos)

        if not self.enabled:
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos) and self.on_click:
                self.on_click()

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False
        self.hovered = False            

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False
        self.hovered = False              

    def activate(self):
        self.show()
        self.enable()

    def deactivate(self):
        self.hide()
        self.disable()

    def draw(self):
        if not self.visible:
            return

        self.hovered = self.enabled and self.rect.collidepoint(pygame.mouse.get_pos())

        if self.enabled:
            if self.hovered:
                bg, border, fg, bw = WHITE, BLACK, BLACK, 2
            else:
                bg, border, fg, bw = WHITE, BLACK, BLACK, 1
        else:
            bg, border, fg, bw = LIGHTGRAY, MIDDLEGRAY, MIDDLEGRAY, 1

        pygame.draw.rect(screen, bg, self.rect)
        pygame.draw.rect(screen, border, self.rect, bw)

        text_on_screen(
            self.text,
            rx=self.rect.centerx / screen.get_width(),
            ry=self.rect.centery / screen.get_height(),
            size=font_size,
            color=fg
        )
#endregion

#region Time
exp_start_perf_counter_time = None
def start_exp_clock():
    global exp_start_perf_counter_time
    exp_start_perf_counter_time = time.perf_counter()

def exp_time():
    return round(time.perf_counter() - exp_start_perf_counter_time, 4)

def secs_since(exp_seconds):
    return round(exp_time() - exp_seconds, 4)
#endregion

#region Exports
def export_dict(d, name):
    os.makedirs(output_path, exist_ok=True)
    full_path = f"{output_path}/{name}.json"
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2, default=str)
    return full_path

def export_list(xs, name):
    os.makedirs(output_path, exist_ok=True)
    full_path = f"{output_path}/{name}.txt"
    with open(full_path, "w", encoding="utf-8") as f:
        for x in xs:
            f.write(str(x) + "\n")
    return full_path

def export_table(rows, name, header=None):
    # rows: list of rows (each row is list/tuple; or dict if header provided)
    os.makedirs(output_path, exist_ok=True)
    full_path = f"{output_path}/{name}.csv"

    with open(full_path, "w", encoding="utf-8", newline="") as f:
        if header is None:
            for r in rows:
                f.write(",".join(str(v) for v in r) + "\n")
        else:
            # header: list of column names
            f.write(",".join(str(h) for h in header) + "\n")
            for r in rows:
                if isinstance(r, dict):
                    f.write(",".join(str(r.get(h, "")) for h in header) + "\n")
                else:
                    f.write(",".join(str(v) for v in r) + "\n")

    return full_path

def flush_experiment(log_message, cause="Finished."):
    global screen
    pygame.display.quit()
    screen = None
    try:
        for data_element in data_to_save:
            content = data_element.get("content", None)
            name = data_element.get("name", "unnamed")
            header = data_element.get("header", None)

            if isinstance(content, dict):
                export_dict(content, name)

            elif isinstance(content, (list, tuple)):
                # decide 1d (.txt) vs 2d (.csv)
                if len(content) == 0:
                    export_list(content, name)

                else:
                    # "2d table" if every row is list/tuple OR dict
                    is_table = all(isinstance(r, (list, tuple, dict)) for r in content)

                    if is_table:
                        export_table(content, name, header=header)
                    else:
                        export_list(content, name)

            else:
                log(f"Unsupported data type for export: {type(content)}", print_to_console=True)
        log("Data successfully exported.")
    except Exception as e:
        origin = _error_origin(e)
        log(f"Could not save data: {origin['file']}:{origin['line']} {repr(e)}")

    log(log_message)
    exp_settings_and_data['end'] = {'time': exp_time(), 'cause': cause}

    if not dev and not demo and on_grid:
        try:
            print("Uploading new data to GitHub, please wait.")
            git_commit_and_sync_from_root("outputs",message="Incoming subject data.")
            print("Success.")
        except Exception as e:
            origin = _error_origin(e)
            log(f"Could not upload data: {origin['file']}:{origin['line']} {repr(e)}")
            print("GitHub data upload failed.")
    
#endregion

#region Logging
log_buffer = []
if os.path.exists(log_path):
    os.remove(log_path)

def log(entry, print_to_console=False, export_to_file=True):
    """
    entry can be: dict/list/str/number/whatever.
    we wrap it into a JSON record with a timestamp.
    """
    # build record with timestamp
    if isinstance(entry, dict):
        record = {"time": exp_time(), **entry}
    else:
        record = {"time": exp_time(), "msg": entry}

    if print_to_console:
        print(record)

    if export_to_file:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        to_write = list(log_buffer)
        to_write.append(record)

        with open(log_path, "a", encoding="utf-8") as f:
            for r in to_write:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

        log_buffer.clear()
    else:
        log_buffer.append(record)

def _error_origin(e):
    tb = traceback.extract_tb(e.__traceback__)
    if not tb:
        return {"file": None, "line": None, "func": None, "code": None}
    f = tb[-1]  # last frame = where the exception was raised
    return {"file": f.filename, "line": f.lineno, "func": f.name, "code": f.line}
    
def handle_error(e):
    origin = _error_origin(e)
    flush_experiment(f"Script reached an error: {repr(e)}", cause=f"{origin['file']}:{origin['line']} {repr(e)}")

#endregion

#endregion

#region Flow control

#region UI elements
consent_radio = RadioButtons(
    ui,
    ["I consent to participate in the experiment"],
    rrect=(0.5, 0.65,  0.3, 0.15),
    on_submit=lambda ans: (
        log({"event": "Consented.", "answer": ans})
    ),
    layout="vertical"
)

screener_radio = RadioButtons(
    ui,
    ["I confirm that none of the above listed items applies to me."],
    rrect=(0.5, 0.65,  0.4, 0.15),
    on_submit=lambda ans: (
        log({"event": "Screened.", "answer": ans})
    ),
    layout="vertical"
)

gender_radio = RadioButtons(
    ui,
    ["female", "male"],
    rrect=(0.5, 0.45,  0.3, 0.15),
    on_submit=lambda ans: (
        log({"event": "Gender submitted.", "answer": ans}),
        subject_data.update({"gender": ans}),
    ),
    allow_other=True,
    other_placeholder="other...",
    header="Gender?",
    layout="horizontal"
)

age_field = TextField(
    ui,
    (0.5, 0.6, 0.06, 0.1),
    placeholder="age...",
    header="Age?",
    on_submit=lambda ans: (
        log({"event": "Age submitted.", "answer": ans}),
        subject_data.update({"age": ans})
    )
)

button_pos_v = 0.9
button_w = 0.15
button_h = 0.08

proceed_button = Button( # Used to initiate stage progression
    ui, 
    (0.5, button_pos_v, button_w, button_h), 
    "Proceed", 
    lambda: (
        log({"event": "Proceed clicked."}),
        increment_stage()
    )       
)

start_button = Button( # Used to initiate 'stimulus' substage
    ui, 
    (0.5, button_pos_v, button_w, button_h), 
    "Start", 
    lambda: (
        log({"event": f"Start clicked for {exp_structure[stage_index]['name']}."}),
        start_stimulus()
    )
)

repeat_button = Button( # Used to re-initiate 'stimulus' substage
    ui, 
    (0.5, button_pos_v - button_h*1.1, button_w, button_h), 
    "Repeat", 
    lambda: (
        log({"event": f"Repeat clicked for {exp_structure[stage_index]['name']}."}),
        start_stimulus()
    )
)

next_button = Button( # Used to proceed to next test condition
    ui, 
    (0.5, button_pos_v, button_w, button_h), 
    "Next", 
    lambda: (
        log({"event": f"Next clicked for {exp_structure[stage_index]['name']}."}),
        change_test_condition()
    )
)

likert_radio_aud = RadioButtons(
    ui,
    header="How difficult was the auditory part? (1 - easy, 5 - hard)",
    options=["1", "2", "3", "4", "5"],
    rrect=(0.5, 0.3,  0.35, 0.1),
    on_submit=lambda ans: (
        log({"event": "Auditory likert submitted.", "answer": ans}),
        subject_data.update({"likert_aud": ans}),
    ),
    layout="horizontal"
)

likert_radio_vis = RadioButtons(
    ui,
    header="How difficult was the visual part? (1 - easy, 5 - hard)",
    options=["1", "2", "3", "4", "5"],
    rrect=(0.5, 0.3+0.25/2,  0.35, 0.1),
    on_submit=lambda ans: (
        log({"event": "Visual likert submitted.", "answer": ans}),
        subject_data.update({"likert_vis": ans}),
    ),
    layout="horizontal"
)

pattern_radio = RadioButtons(
    ui,
    header="Did you see any patterns?",
    options=["yes", "no"],
    rrect=(0.5, 0.55,  0.2, 0.1),
    on_submit=lambda ans: (
        log({"event": "Pattern reply submitted.", "answer": ans}),
        subject_data.update({"patterns_saw": ans}),
    ),
    layout="horizontal"
)

strategy_field = TextField(
    ui,
    (0.5, 0.725, 0.65, 0.2),
    header="Explain your strategy:",
    placeholder="Full sentence reply please...",
    on_submit=lambda ans: (
        log({"event": "Strategy submitted.", "answer": ans}),
        subject_data.update({"strategy": ans})
    )
)
#endregion

#region Core functions

def change_test_condition():
    global test_condition_index

    if test_conditions_left() > 0:
        test_condition_index += 1
        log(f"Test condition changed to {exp_structure[stage_index]['conditions'][test_condition_index]} in {exp_structure[stage_index]['name']}.")
        next_button.deactivate()
        start_button.disable()
        set_up_stage()

def test_conditions_left():
    if is_test_stage():
        return len(exp_structure[stage_index]['conditions']) - (test_condition_index) - 1
    return 0

def all_test_conditions_done():
    if is_test_stage():
        return test_conditions_left() == 0
    else:
        return True

def start_stimulus():
    global substage, log_space_press, repeat_num

    if repeat_num is None:
        repeat_num = 0
    else:
        repeat_num += 1

    substage = 'stimulus'
    start_button.deactivate()
    repeat_button.deactivate()
    proceed_button.deactivate()

    stage = exp_structure[stage_index]
    
    match stage['type']:
        case 'demo_stimulus':
            subject_data['repeat_num'][stage['name']] = repeat_num
            subject_data['space_presses'][stage['name']][f"repeat_{repeat_num}"] = []
            log_space_press = True
            match stage['modifiers']['modality']:
                case 'aud':
                    play_auditory_stimulus(
                            speaker_routing = routing['demo_aud'],
                            samp = stimuli['demo_aud'],
                            start_in_s = 0
                            )
                case 'vis':
                    play_visual_stimulus(stimuli['demo_vis'])
            log(f"Start stimulus for {stage['name']}.")
        case 'familiarization':
            subject_data['repeat_num'][stage['name']] = repeat_num
            match stage['modifiers']['modality']:
                case 'aud':
                    play_auditory_stimulus(
                        speaker_routing = routing[stage['name']],
                        samp=stimuli[stage['name']], 
                        start_in_s = 
                        0)
                case 'vis':
                    play_visual_stimulus(stimuli[stage['name']])
            log(f"Start stimulus for {stage['name']}.")
        case 'practice':
            subject_data['repeat_num'][stage['name']] = repeat_num
            subject_data['space_presses'][stage['name']][f"repeat_{repeat_num}"] = []
            log_space_press = True
            match stage['modifiers']['modality']:
                case 'aud':
                    play_auditory_stimulus(
                        speaker_routing = routing[stage['name']],
                        samp=stimuli[stage['name']],
                        start_in_s = 0
                        )
                case 'vis':
                    play_visual_stimulus(stimuli[stage['name']])
            log(f"Start stimulus for {stage['name']}, repeat {repeat_num}.")
        case 'test':
            log_space_press = True
            match stage['modifiers']['modality']:
                case 'aud':
                    play_auditory_stimulus(
                        speaker_routing = routing[stage['name']],
                        samp=stimuli[stage['name']][stage['conditions'][test_condition_index]],
                        start_in_s = 0
                        )
                case 'vis':
                    play_visual_stimulus(stimuli[stage['name']][stage['conditions'][test_condition_index]])
            log(f"Start stimulus for {stage['name']} in condition {stage['conditions'][test_condition_index]}.")
        case _:
            pass

def stop_stimulus():
    global substage, log_space_press, stimulus_start
    stop_buffered_audio()
    audio_stop('fade',fade_ms = 120)
    video_stop()
    log(f"Finished stimulus for {exp_structure[stage_index]['name']}.")
    substage = 'repeat'

    if is_test_stage():
        if all_test_conditions_done():
            next_button.deactivate()
            proceed_button.activate()
        else:
            next_button.activate()
    else:
        repeat_button.activate()
        proceed_button.activate()

    log_space_press = False
    stimulus_start = None

def compute_stimulus_finished():
    '''
    Example structure for now, specify according to experimental design.
    '''
    match exp_structure[stage_index]['modifiers']['modality']:
        case 'aud':
            return not audio_is_playing()
        case 'vis':
            return not video_is_playing()
        case _:
            return None
        
def is_test_stage():
    return 'conditions' in exp_structure[stage_index]

def is_stimulus_stage():
    return 'modifiers' in exp_structure[stage_index]

def set_up_stage():
    global stage_start, substage, test_condition_index
    
    stage = exp_structure[stage_index]
    
    log(f"Set up {stage['name']} stage.")
    
    stage_start = exp_time()
    match stage['type']:
        case 'demo_welcome':
            proceed_button.show()
        case 'demo_stimulus':
            substage = 'intro'
            start_button.show()
            subject_data['space_presses'][stage['name']] = {}
        case 'demo_thanks':
            pass
        case 'welcome':
            proceed_button.show()
            consent_radio.show()
        case 'screener':
            proceed_button.show()
            screener_radio.show()
        case 'intro_questionnaire':
            proceed_button.show()
            gender_radio.show()
            age_field.show()
        case 'familiarization':
            substage = 'intro'
            start_button.show()
        case 'practice':
            substage = 'intro'
            start_button.show()
            subject_data['space_presses'][stage['name']] = {}
        case 'test':
            if test_condition_index is None: # First start up of stage
                test_condition_index = 0
                subject_data['space_presses'][stage['name']] = {}
            subject_data['space_presses'][stage['name']][stage['conditions'][test_condition_index]] = []
            substage = 'intro'
            start_button.show()
        case 'outro_questionnaire':
            proceed_button.show()
            likert_radio_aud.show()
            likert_radio_vis.show()
            pattern_radio.show()
            strategy_field.show()
        case 'thanks':
            pass
        case _:
            finish("Undefined stage progression.")

def shutdown_stage():
    global stage, stage_start, substage, stage_completed, stimulus_finished, stimulus_start, test_condition_index, repeat_num

    # Reset flags
    stage_start = None
    substage = None
    stimulus_finished = None
    stimulus_start = None
    stage_completed = False
    test_condition_index = None
    repeat_num = None

    stage = exp_structure[stage_index]
    
    log(f"Shut down {stage['name']} stage.")

    match stage['type']:
        case 'demo_welcome':
            proceed_button.deactivate()
        case 'demo_thanks':
            pass
        case 'welcome':
            proceed_button.deactivate()
            consent_radio.submit()
            consent_radio.hide()
        case 'screener':
            proceed_button.deactivate()
            screener_radio.submit()
            screener_radio.hide()
        case 'intro_questionnaire':
            gender_radio.submit()
            age_field.submit()
            gender_radio.hide()
            age_field.hide()
            proceed_button.deactivate()
        case 'demo_stimulus':
            start_button.deactivate()
            repeat_button.deactivate()
            proceed_button.deactivate()
        case 'familiarization':
            start_button.deactivate()
            repeat_button.deactivate()
            proceed_button.deactivate()
        case 'practice':
            start_button.deactivate()
            repeat_button.deactivate()
            proceed_button.deactivate()
        case 'test':
            start_button.deactivate()
            repeat_button.deactivate()
            proceed_button.deactivate()
            next_button.deactivate()
        case 'outro_questionnaire':
            proceed_button.deactivate()
            strategy_field.submit()
            strategy_field.hide()
            likert_radio_aud.submit()
            likert_radio_aud.hide()
            likert_radio_vis.submit()
            likert_radio_vis.hide()
            pattern_radio.submit()
            pattern_radio.hide()
        case _:
            pass

def increment_stage():
    global stage_index

    # Shut down this stage
    shutdown_stage()
    
    # Increment stage
    stage_index += 1

    # Start up next stage
    set_up_stage()

def finish(cause):
    '''
    Set flag so that script will finish the current cycle then quit.
    End experiment, do cleanup, export and save what needs to be, etc.
    '''
    print("Exit main experimental loop.\n")
    global running
    running = False
    flush_experiment(cause, cause)

def draw():
    '''
    Draw new frame.
    '''
    if not screen:
        return
    global dev_message

    stage = exp_structure[stage_index]

    # Draw to buffer
    dev_message = f'stage: {stage["name"]}'

    match stage['type']:
        case 'demo_welcome':
            screen.fill(WHITE)
            wrapped_text_on_screen(
                instructions[stage['name']],
                (0.5, 0.25, 0.8, 0.3)   # (rcx, rcy, rw, rh) in screen-relative units
            )
        case 'demo_thanks':
            screen.fill(WHITE)
            wrapped_text_on_screen(
                instructions[stage['name']],
                (0.5, 0.25, 0.8, 0.3)   # (rcx, rcy, rw, rh) in screen-relative units
            )
        case 'welcome':
            screen.fill(WHITE)
            wrapped_text_on_screen(
                instructions[stage['name']],
                (0.5, 0.25, 0.8, 0.3)   # (rcx, rcy, rw, rh) in screen-relative units
            )
        case 'screener':
            screen.fill(WHITE)
            wrapped_text_on_screen(
                instructions[stage['name']],
                (0.5, 0.25, 0.8, 0.3)   # (rcx, rcy, rw, rh) in screen-relative units
            )
        case 'intro_questionnaire':
            screen.fill(WHITE)
            wrapped_text_on_screen(
                instructions[stage['name']],
                (0.5, 0.25, 0.8, 0.3)
            )
        case 'demo_stimulus':
            match substage:
                case 'intro':
                    screen.fill(WHITE)
                    wrapped_text_on_screen(
                        instructions[stage['name']],
                        (0.5, 0.25, 0.8, 0.3)
                    )
                case 'stimulus':
                    screen.fill(LIGHTGRAY)
                    video_tick()
                case 'repeat':
                    screen.fill(WHITE)
                    text_on_screen('<want repeat?>', 0.5, 0.1)
        case 'familiarization':
            match substage:
                case 'intro':
                    screen.fill(WHITE)
                    wrapped_text_on_screen(
                        instructions[stage['name']],
                        (0.5, 0.25, 0.8, 0.3)
                    )
                case 'stimulus':
                    screen.fill(LIGHTGRAY)
                    text_on_screen('Could you catch that a few of the pauses were longer?', 0.5, 0.1)
                    video_tick()
                case 'repeat':
                    screen.fill(WHITE)
                    text_on_screen('<want repeat?>', 0.5, 0.1)
        case 'practice':
            match substage:
                case 'intro':
                    screen.fill(WHITE)
                    wrapped_text_on_screen(
                        instructions[stage['name']],
                        (0.5, 0.25, 0.8, 0.3)
                    )
                case 'stimulus':
                    screen.fill(LIGHTGRAY)
                    match stage['modifiers']['modality']:
                        case 'aud':
                            text_on_screen('<playing sound and logging SPACE>', 0.5, 0.1)
                        case 'vis':
                            text_on_screen('<showing video and logging SPACE>', 0.5, 0.1)
                            video_tick()
                case 'repeat':
                    screen.fill(WHITE)
                    text_on_screen('<want repeat?>', 0.5, 0.1)
        case 'test':
            match substage:
                case 'intro':
                    screen.fill(WHITE)
                    wrapped_text_on_screen(
                        instructions[stage['name']],
                        (0.5, 0.25, 0.8, 0.3)
                    )
                case 'stimulus':
                    screen.fill(LIGHTGRAY)
                    match stage['modifiers']['modality']:
                        case 'aud':
                            text_on_screen(f'<condition: {stage["conditions"][test_condition_index]}>', 0.5, 0.075)
                            text_on_screen('<playing sound and logging SPACE>', 0.5, 0.125)
                        case 'vis':
                            text_on_screen(f'<condition: {stage["conditions"][test_condition_index]}>', 0.5, 0.075)
                            text_on_screen('<showing video and logging SPACE>', 0.5, 0.125)
                            video_tick()
                case 'repeat':
                    screen.fill(WHITE)
                    if all_test_conditions_done():
                        text_on_screen('<test stage finished>', 0.5, 0.1)
                    else:
                        text_on_screen('<next condition?>', 0.5, 0.1)
        case 'outro_questionnaire':
            screen.fill(WHITE)
            wrapped_text_on_screen(
                        instructions[stage['name']],
                        (0.5, 0.15, 0.8, 0.3)
                    )
        case 'thanks':
            screen.fill(WHITE)
            wrapped_text_on_screen(
                        instructions[stage['name']],
                        (0.5, 0.25, 0.8, 0.3)
                    )

    for u in ui:
        u.draw()

    if dev or demo:
        if is_stimulus_stage() and stimulus_start:
            dev_message = f"{dev_message}, secs = {secs_since(stimulus_start)}"
        text_on_screen(dev_message, 0.025, 0.08, font_size, RED, bg = GREEN, bg_alpha=0.25, align = "left")

    # Render to display
    pygame.display.flip()

def on_key_press(k):
    '''
    Handle keyboard-based user interaction.
    '''

    stage = exp_structure[stage_index]

    if k == pygame.K_ESCAPE:
        finish("User interrupt.")
    elif k == pygame.K_SPACE and log_space_press:
        log(f"Space pressed in stage {stage['name']}.")
        if is_test_stage():
            subject_data['space_presses'][stage['name']][stage['conditions'][test_condition_index]].append(secs_since(stimulus_start))
        else:
            subject_data['space_presses'][stage['name']][f"repeat_{repeat_num}"].append(secs_since(stimulus_start))
    elif k in (pygame.K_q, pygame.K_q):
        log(f"Q pressed in stage {stage['name']}.")
        if is_stimulus_stage() and substage == 'stimulus':
            stop_stimulus()
    pass

def on_mouse_press(pos, button):
    '''
    Handle mouse-based user interaction except for button presses.
    '''
    pass

def refresh():
    """
    One per-frame state updater.
    """
    global stage_completed, stimulus_finished

    stage = exp_structure[stage_index]

    # Stage completion conditionals
    match stage['type']:
        case 'welcome':
            stage_completed = secs_since(stage_start) > (1 if dev else 10) and consent_radio.get() is not None
        case 'screener':
            stage_completed = secs_since(stage_start) > (1 if dev else 10) and screener_radio.get() is not None
        case 'intro_questionnaire':
            stage_completed = (
                gender_radio.get() not in [None, "", " ", "  "]
                and age_field.get() not in [None, "", " ", "  "]
            )
        case 'outro_questionnaire':
            stage_completed = (
                likert_radio_aud.get() is not None
                and likert_radio_vis.get() is not None
                and pattern_radio.get() is not None
                and strategy_field.get() not in [None, "", " ", "  "]
                )
        case 'thanks':
            stage_completed = secs_since(stage_start) > 2
            if stage_completed:
                finish("End.")
        case 'demo_thanks':
            stage_completed = secs_since(stage_start) > 2
            if stage_completed:
                finish("End.")
        case _:
            stage_completed = True

    if stage_completed:
        proceed_button.enable()
    else:
        proceed_button.disable()

    # Substage management for stimulus stages
    if is_stimulus_stage():
        match substage:
            case 'intro':
                if secs_since(stage_start) > 1:  # DEV
                    start_button.enable()
            case 'stimulus':
                stimulus_finished = compute_stimulus_finished()  # Keep this modular for various finish conditionals
                if stimulus_finished is True:
                    stop_stimulus()
            case _:
                pass
#endregion

#region Main loop
print("\nEnter main experimental loop.")
focus_pygame_window()
print("Use Alt + Tab to bring experiment window in focus.")
dev_message = ''
stage_index = 0
start_exp_clock()
running = True
log("Start.")
set_up_stage()
try:
    while running:
        for event in pygame.event.get():
            for u in ui:
                u.handle_event(event)

            if event.type == pygame.QUIT:
                finish()
            elif event.type == pygame.KEYDOWN:
                on_key_press(event.key)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                on_mouse_press(event.pos, event.button)

        refresh()

        draw()

        clock.tick(60)

except Exception as e:
    handle_error(e)
    raise  # remove to suppress crash

finally:
    mic_meter_stop()
    audio_stop()
    pygame.quit()
#endregion

#endregion