"""
comment_reader.py
YouTube Live / Twitch のコメントを取得して VOICEVOX で読み上げる OBS スクリプト

依存ライブラリ:
  pip install pytchat websocket-client requests sounddevice numpy
"""

import obspython as obs
import threading
import queue
import time
import re
import io
import wave
import subprocess
import requests

# ─── 設定（OBS スクリプト設定画面で変更可能） ────────────────────────────────

cfg = {
    "voicevox_url":    "http://localhost:50021",
    "voicevox_engine": r"C:\Users\YOUR_USERNAME\AppData\Local\Programs\VOICEVOX\vv-engine\run.exe",
    "speaker_id":      3,              # 3=ずんだもん, 1=四国めたん 等
    "max_text_len":    40,             # 読み上げ最大文字数
    "read_username":   True,           # ユーザー名を読み上げるか
    "audio_device":    "CABLE Input",  # VB-Audio デバイス名（部分一致）

    # 音声パラメータ
    "volume_scale":     1.0,   # ボリューム        0.0〜2.0
    "speed_scale":      1.0,   # 速度              0.5〜2.0
    "pitch_scale":      0.0,   # ピッチ           -0.15〜0.15
    "intonation_scale": 1.0,   # イントネーション   0.0〜2.0
    "pre_phoneme_len":  0.1,   # 息継ぎ（前）      0.0〜1.5
    "post_phoneme_len": 0.1,   # 息継ぎ（後）      0.0〜1.5

    # YouTube
    "yt_enabled":     True,
    "yt_channel_url": "",

    # フィルタ
    "block_users": "",  # 読み上げ除外ユーザー（カンマ区切り、小文字）

    # Twitch
    "tw_enabled": True,
    "tw_channel": "your_channel",
    "tw_oauth":   "",
    "tw_nick":    "your_channel",
}

# ─── 内部状態 ────────────────────────────────────────────────────────────────

tts_queue:    queue.Queue = queue.Queue()
running       = False
yt_thread     = None
tw_thread     = None
tts_thread    = None
voicevox_proc = None

# ─── VOICEVOX エンジン 起動 / 終了 ───────────────────────────────────────────

def start_voicevox():
    global voicevox_proc
    exe = cfg["voicevox_engine"]
    print(f"[VOICEVOX] 起動中: {exe}")
    try:
        voicevox_proc = subprocess.Popen(
            [exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"[VOICEVOX] 起動失敗: {e}")
        return
    for _ in range(30):
        try:
            requests.get(f"{cfg['voicevox_url']}/version", timeout=1)
            print("[VOICEVOX] 起動完了")
            return
        except Exception:
            time.sleep(1)
    print("[VOICEVOX] 起動タイムアウト（続行します）")


def stop_voicevox():
    global voicevox_proc
    if voicevox_proc and voicevox_proc.poll() is None:
        voicevox_proc.terminate()
        try:
            voicevox_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            voicevox_proc.kill()
        print("[VOICEVOX] 終了しました")
    voicevox_proc = None

# ─── VOICEVOX + sounddevice 読み上げ ─────────────────────────────────────────

def find_device_index(name_hint: str):
    try:
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if name_hint.lower() in d["name"].lower() and d["max_output_channels"] > 0:
                return i
    except Exception as e:
        print(f"[TTS] デバイス検索失敗: {e}")
    return None


def speak(text: str):
    try:
        import sounddevice as sd
        import numpy as np

        r = requests.post(
            f"{cfg['voicevox_url']}/audio_query",
            params={"text": text, "speaker": cfg["speaker_id"]},
            timeout=5,
        )
        r.raise_for_status()
        query = r.json()

        # 音声パラメータを上書き
        query["volumeScale"]       = cfg["volume_scale"]
        query["speedScale"]        = cfg["speed_scale"]
        query["pitchScale"]        = cfg["pitch_scale"]
        query["intonationScale"]   = cfg["intonation_scale"]
        query["prePhonemeLength"]  = cfg["pre_phoneme_len"]
        query["postPhonemeLength"] = cfg["post_phoneme_len"]

        audio_res = requests.post(
            f"{cfg['voicevox_url']}/synthesis",
            params={"speaker": cfg["speaker_id"]},
            json=query,
            timeout=10,
        )
        audio_res.raise_for_status()

        wav_bytes = io.BytesIO(audio_res.content)
        with wave.open(wav_bytes) as wf:
            samplerate = wf.getframerate()
            n_channels = wf.getnchannels()
            frames     = wf.readframes(wf.getnframes())

        audio_np = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        if n_channels > 1:
            audio_np = audio_np.reshape(-1, n_channels)

        device_idx = find_device_index(cfg["audio_device"])
        sd.play(audio_np, samplerate=samplerate, device=device_idx)
        sd.wait()

    except Exception as e:
        print(f"[TTS] Error: {e}")


def tts_worker():
    while running:
        try:
            text = tts_queue.get(timeout=1)
            speak(text)
            tts_queue.task_done()
        except queue.Empty:
            continue


def is_blocked(name: str) -> bool:
    blocked = [u.strip().lower() for u in cfg["block_users"].split(",") if u.strip()]
    return name.lower() in blocked


def build_read_text(name: str, message: str) -> str:
    message = message[:cfg["max_text_len"]]
    if cfg["read_username"] and name:
        return f"{name}、{message}"
    return message

# ─── YouTube: 進行中ライブの video_id を自動取得 ─────────────────────────────

YT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
}


def fetch_live_video_id(channel_url: str):
    live_url = channel_url.rstrip("/") + "/live"
    try:
        res = requests.get(live_url, headers=YT_HEADERS, timeout=10, allow_redirects=True)
        m = re.search(r"watch\?v=([a-zA-Z0-9_-]{11})", res.url)
        if m:
            return m.group(1)
        matches = re.findall(r'"videoId"\s*:\s*"([a-zA-Z0-9_-]{11})"', res.text)
        if matches:
            return matches[0]
    except Exception as e:
        print(f"[YT] video_id 取得失敗: {e}")
    return None

# ─── YouTube コメント取得（pytchat） ─────────────────────────────────────────

def yt_worker():
    try:
        import pytchat
    except ImportError:
        print("[YT] pytchat がインストールされていません: pip install pytchat")
        return

    while running:
        channel_url = cfg["yt_channel_url"].strip()
        if not channel_url:
            time.sleep(5)
            continue

        print("[YT] ライブ検索中...")
        video_id = fetch_live_video_id(channel_url)

        if not video_id:
            print("[YT] 進行中のライブが見つかりません。30秒後に再試行...")
            time.sleep(30)
            continue

        print(f"[YT] 接続中: {video_id}")
        try:
            chat = pytchat.create(video_id=video_id)
            while chat.is_alive() and running:
                for c in chat.get().sync_items():
                    if not running:
                        break
                    if not is_blocked(c.author.name):
                        tts_queue.put(build_read_text(c.author.name, c.message))
                time.sleep(1)
            chat.terminate()
            print("[YT] 配信終了または切断")
        except Exception as e:
            print(f"[YT] エラー: {e}")

        if running:
            print("[YT] 30秒後に再接続...")
            time.sleep(30)

# ─── Twitch コメント取得（IRC over WebSocket） ────────────────────────────────

def tw_worker():
    try:
        import websocket
    except ImportError:
        print("[TW] websocket-client がインストールされていません: pip install websocket-client")
        return

    channel = f"#{cfg['tw_channel'].lstrip('#')}"
    IRC_URL = "wss://irc-ws.chat.twitch.tv:443"

    while running:
        oauth = cfg["tw_oauth"].strip()
        if not oauth:
            print("[TW] OAuth トークンが未設定です")
            time.sleep(10)
            continue

        print(f"[TW] 接続中: {channel}")
        try:
            ws = websocket.create_connection(IRC_URL, sslopt={"cert_reqs": 0})
            ws.send(f"PASS {oauth}")
            ws.send(f"NICK {cfg['tw_nick']}")
            ws.send(f"JOIN {channel}")
            ws.settimeout(30)

            while running:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    ws.send("PING :tmi.twitch.tv")
                    continue

                for line in raw.strip().split("\r\n"):
                    if line.startswith("PING"):
                        ws.send("PONG :tmi.twitch.tv")
                    elif "PRIVMSG" in line:
                        try:
                            name    = line.split("!")[0][1:]
                            message = line.split("PRIVMSG")[1].split(":", 1)[1].strip()
                            if message.startswith("!"):
                                continue
                            if not is_blocked(name):
                                tts_queue.put(build_read_text(name, message))
                        except Exception:
                            pass
            ws.close()
        except Exception as e:
            print(f"[TW] エラー: {e}")

        if running:
            print("[TW] 5秒後に再接続...")
            time.sleep(5)

# ─── OBS スクリプト API ───────────────────────────────────────────────────────

def script_description():
    return (
        "YouTube Live / Twitch のコメントを VOICEVOX で読み上げます。\n\n"
        "VOICEVOX エンジンはスクリプト起動時に自動起動・終了時に自動終了します。\n\n"
        "【音声デバイス】VB-Audio に流す場合は「CABLE Input」と入力。\n"
        "【YouTube】チャンネル URL を設定するだけで配信開始を自動検出。\n"
        "【Twitch】OAuth: https://twitchapps.com/tmi/ で取得。\n\n"
        "pip install pytchat websocket-client requests sounddevice numpy"
    )


def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "voicevox_url",      cfg["voicevox_url"])
    obs.obs_data_set_default_string(settings, "voicevox_engine",   cfg["voicevox_engine"])
    obs.obs_data_set_default_int   (settings, "speaker_id",        cfg["speaker_id"])
    obs.obs_data_set_default_int   (settings, "max_text_len",      cfg["max_text_len"])
    obs.obs_data_set_default_bool  (settings, "read_username",     cfg["read_username"])
    obs.obs_data_set_default_string(settings, "audio_device",      cfg["audio_device"])
    obs.obs_data_set_default_double(settings, "volume_scale",      cfg["volume_scale"])
    obs.obs_data_set_default_double(settings, "speed_scale",       cfg["speed_scale"])
    obs.obs_data_set_default_double(settings, "pitch_scale",       cfg["pitch_scale"])
    obs.obs_data_set_default_double(settings, "intonation_scale",  cfg["intonation_scale"])
    obs.obs_data_set_default_double(settings, "pre_phoneme_len",   cfg["pre_phoneme_len"])
    obs.obs_data_set_default_double(settings, "post_phoneme_len",  cfg["post_phoneme_len"])
    obs.obs_data_set_default_string(settings, "block_users",       cfg["block_users"])
    obs.obs_data_set_default_bool  (settings, "yt_enabled",        cfg["yt_enabled"])
    obs.obs_data_set_default_string(settings, "yt_channel_url",    "")
    obs.obs_data_set_default_bool  (settings, "tw_enabled",        cfg["tw_enabled"])
    obs.obs_data_set_default_string(settings, "tw_channel",        cfg["tw_channel"])
    obs.obs_data_set_default_string(settings, "tw_nick",           cfg["tw_nick"])
    obs.obs_data_set_default_string(settings, "tw_oauth",          "")


def script_properties():
    props = obs.obs_properties_create()

    obs.obs_properties_add_text  (props, "voicevox_url",     "VOICEVOX URL",              obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text  (props, "voicevox_engine",  "VOICEVOX エンジン exe パス", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_int   (props, "speaker_id",       "スピーカー ID",              0, 100, 1)
    obs.obs_properties_add_int   (props, "max_text_len",     "読み上げ最大文字数",          10, 200, 5)
    obs.obs_properties_add_bool  (props, "read_username",    "ユーザー名を読み上げる")
    obs.obs_properties_add_text  (props, "audio_device",     "音声出力デバイス名",          obs.OBS_TEXT_DEFAULT)

    obs.obs_properties_add_float_slider(props, "volume_scale",    "ボリューム",        0.0,  2.0,  0.05)
    obs.obs_properties_add_float_slider(props, "speed_scale",     "読み上げ速度",      0.5,  2.0,  0.05)
    obs.obs_properties_add_float_slider(props, "pitch_scale",     "ピッチ",           -0.15, 0.15, 0.01)
    obs.obs_properties_add_float_slider(props, "intonation_scale","イントネーション",   0.0,  2.0,  0.05)
    obs.obs_properties_add_float_slider(props, "pre_phoneme_len", "息継ぎ（前）",      0.0,  1.5,  0.05)
    obs.obs_properties_add_float_slider(props, "post_phoneme_len","息継ぎ（後）",      0.0,  1.5,  0.05)

    obs.obs_properties_add_text  (props, "block_users",      "読み上げ除外ユーザー（カンマ区切り）", obs.OBS_TEXT_DEFAULT)

    obs.obs_properties_add_bool  (props, "yt_enabled",       "YouTube 有効")
    obs.obs_properties_add_text  (props, "yt_channel_url",   "YouTube チャンネル URL",     obs.OBS_TEXT_DEFAULT)

    obs.obs_properties_add_bool  (props, "tw_enabled",       "Twitch 有効")
    obs.obs_properties_add_text  (props, "tw_channel",       "Twitch チャンネル名",         obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text  (props, "tw_nick",          "Twitch ユーザー名",           obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text  (props, "tw_oauth",         "Twitch OAuth トークン",       obs.OBS_TEXT_PASSWORD)

    return props


def script_update(settings):
    cfg["voicevox_url"]    = obs.obs_data_get_string(settings, "voicevox_url")
    cfg["voicevox_engine"] = obs.obs_data_get_string(settings, "voicevox_engine")
    cfg["speaker_id"]      = obs.obs_data_get_int   (settings, "speaker_id")
    cfg["max_text_len"]    = obs.obs_data_get_int   (settings, "max_text_len")
    cfg["read_username"]   = obs.obs_data_get_bool  (settings, "read_username")
    cfg["audio_device"]    = obs.obs_data_get_string(settings, "audio_device")
    cfg["volume_scale"]    = obs.obs_data_get_double(settings, "volume_scale")
    cfg["speed_scale"]     = obs.obs_data_get_double(settings, "speed_scale")
    cfg["pitch_scale"]     = obs.obs_data_get_double(settings, "pitch_scale")
    cfg["intonation_scale"]= obs.obs_data_get_double(settings, "intonation_scale")
    cfg["pre_phoneme_len"] = obs.obs_data_get_double(settings, "pre_phoneme_len")
    cfg["post_phoneme_len"]= obs.obs_data_get_double(settings, "post_phoneme_len")
    cfg["block_users"]     = obs.obs_data_get_string(settings, "block_users")
    cfg["yt_enabled"]      = obs.obs_data_get_bool  (settings, "yt_enabled")
    cfg["yt_channel_url"]  = obs.obs_data_get_string(settings, "yt_channel_url")
    cfg["tw_enabled"]      = obs.obs_data_get_bool  (settings, "tw_enabled")
    cfg["tw_channel"]      = obs.obs_data_get_string(settings, "tw_channel")
    cfg["tw_nick"]         = obs.obs_data_get_string(settings, "tw_nick")
    cfg["tw_oauth"]        = obs.obs_data_get_string(settings, "tw_oauth")


def script_load(settings):
    global running, yt_thread, tw_thread, tts_thread

    script_update(settings)

    threading.Thread(target=start_voicevox, daemon=True).start()

    running = True

    tts_thread = threading.Thread(target=tts_worker, daemon=True)
    tts_thread.start()

    if cfg["yt_enabled"]:
        yt_thread = threading.Thread(target=yt_worker, daemon=True)
        yt_thread.start()

    if cfg["tw_enabled"]:
        tw_thread = threading.Thread(target=tw_worker, daemon=True)
        tw_thread.start()

    print("[CommentReader] 起動しました")
    print(f"[CommentReader] 音声出力デバイス: {cfg['audio_device']}")


def script_unload():
    global running
    running = False
    stop_voicevox()
    print("[CommentReader] 停止しました")
