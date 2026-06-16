"""
comment_reader.py
YouTube Live / Twitch のコメントを取得して VOICEVOX で読み上げる OBS スクリプト

依存ライブラリ:
  pip install websocket-client requests sounddevice numpy
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
    # VOICEVOX エンジン exe パス（環境に合わせて変更）
    # 標準インストール先: C:\Users\<ユーザー名>\AppData\Local\Programs\VOICEVOX\vv-engine\run.exe
    "voicevox_engine": r"C:\Users\YOUR_USERNAME\AppData\Local\Programs\VOICEVOX\vv-engine\run.exe",

    # スピーカー ID（VOICEVOX API の speaker パラメータ）
    # ─────────────────────────────────────────────────────
    # 四国めたん : ノーマル=2  あまあま=0  ツンツン=6  セクシー=4  ささやき=36  ヒソヒソ=37
    # ずんだもん : ノーマル=3  あまあま=1  ツンツン=7  セクシー=5  ささやき=22  ヒソヒソ=38  ヘロヘロ=75  なみだめ=76
    # 春日部つむぎ: ノーマル=8
    # 雨晴はう   : ノーマル=10
    # 波音リツ   : ノーマル=9   クイーン=65
    # 玄野武宏   : ノーマル=11  喜び=39    ツンギレ=40  悲しみ=41
    # 白上虎太郎 : ふつう=12   わーい=32   びくびく=33  おこ=34    びえーん=35
    # 青山龍星   : ノーマル=13  熱血=81    不機嫌=82    喜び=83    しっとり=84  かなしみ=85  囁き=86
    # 冥鳴ひまり : ノーマル=14
    # 九州そら   : ノーマル=16  あまあま=15 ツンツン=17  セクシー=18  ささやき=19
    # ※ 起動中のVOICEVOXエンジンに GET /speakers でフルリストを確認できます
    "speaker_id":      3,   # デフォルト: ずんだもん ノーマル

    "max_text_len":    40,             # 読み上げ最大文字数（comment_len_limit_enabled=False 時のフォールバック）
    "read_username":   True,           # ユーザー名を読み上げるか
    "audio_device":    "CABLE Input",  # VB-Audio デバイス名（部分一致） / NVIDIA Broadcast 等も可

    # 音声パラメータ（VOICEVOX audio_query の各フィールドに対応）
    "volume_scale":     1.0,   # ボリューム        0.0〜2.0
    "speed_scale":      1.0,   # 読み上げ速度      0.5〜2.0
    "pitch_scale":      0.0,   # ピッチ           -0.15〜0.15
    "intonation_scale": 1.0,   # イントネーション   0.0〜2.0
    "pre_phoneme_len":  1.0,   # 息継ぎ（前）      0.0〜1.5
    "post_phoneme_len": 1.0,   # 息継ぎ（後）      0.0〜1.5

    # YouTube
    "yt_enabled":     True,
    "yt_channel_url": "",     # 例: https://www.youtube.com/@your_channel

    # フィルタ
    "block_users": "",  # 読み上げ除外ユーザー（カンマ区切り、アカウント名で指定）

    # 文字数制限
    "comment_len_limit_enabled": True,   # コメント読み上げ文字数制限 有効/無効
    "comment_len_limit":         50,     # コメント読み上げ最大文字数
    "name_len_limit_enabled":    True,   # 名前読み上げ文字数制限 有効/無効
    "name_len_limit":            10,     # 名前読み上げ最大文字数

    # フィルタ追加オプション
    "exclude_emoji":             False,  # 絵文字を除外する
    "exclude_yt_stamp":          False,  # カスタムスタンプを読み上げない（YouTube）
    "exclude_gift":              False,  # ギフト画像を読み上げない
    "emoji_image_limit_enabled": False,  # 絵文字/画像数制限
    "emoji_image_limit":         5,      # 絵文字/画像 上限数
    "read_system_message":       False,  # システムメッセージの読み上げ

    # 読み上げフォーマット
    "block_comments":       "",                  # 読み上げ除外ワード・フレーズ（行区切り）
    "read_format":          "{name}。{comment}", # 通常コメントの読み上げフォーマット
    "notify_read_format":   "{comment}",         # 通知メッセージの読み上げフォーマット

    # Twitch
    "tw_enabled": True,
    "tw_channel": "",   # Twitch チャンネル名（例: asai_501xx）
    "tw_oauth":   "",   # OAuth トークン: https://twitchapps.com/tmi/ で取得
    "tw_nick":    "",   # Twitch ログインユーザー名（チャンネル名と同じでよい場合が多い）
}

# ─── 内部状態 ────────────────────────────────────────────────────────────────

tts_queue:    queue.Queue = queue.Queue()
running       = False
yt_thread     = None
tw_thread     = None
tts_thread    = None
voicevox_proc = None
seen_yt_ids: set = set()  # YouTube既読メッセージID

# ─── VOICEVOX エンジン 起動 / 終了 ───────────────────────────────────────────

def start_voicevox():
    global voicevox_proc

    # すでに起動済みか確認
    try:
        requests.get(f"{cfg['voicevox_url']}/version", timeout=1)
        print("[VOICEVOX] 既に起動済み。自動起動をスキップします")
        return
    except Exception:
        pass

    exe = cfg["voicevox_engine"]
    print(f"[VOICEVOX] 起動中: {exe}")
    try:
        voicevox_proc = subprocess.Popen(
            [exe],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
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


def is_comment_blocked(text: str) -> bool:
    """読み上げ除外フレーズが含まれていればTrueを返す"""
    phrases = [p.strip() for p in cfg["block_comments"].splitlines() if p.strip()]
    for phrase in phrases:
        if phrase.lower() in text.lower():
            return True
    return False


def _strip_emoji(text: str) -> str:
    """Unicode絵文字を除去する"""
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F9FF"
        "\U00002600-\U000027BF"
        "\U0001FA00-\U0001FA6F"
        "\U0001FA70-\U0001FAFF"
        "\u2702-\u27B0"
        "]+",
        flags=re.UNICODE,
    )
    return emoji_pattern.sub("", text).strip()


def build_read_text(name: str, message: str, is_notify: bool = False) -> str:
    # 絵文字除外
    if cfg["exclude_emoji"]:
        message = _strip_emoji(message)
        name    = _strip_emoji(name)

    # コメント文字数制限
    if cfg["comment_len_limit_enabled"]:
        message = message[:cfg["comment_len_limit"]]
    else:
        message = message[:cfg["max_text_len"]]  # 従来のフォールバック

    # 名前文字数制限
    if cfg["name_len_limit_enabled"]:
        name = name[:cfg["name_len_limit"]]

    # フォーマット適用
    if is_notify:
        fmt = cfg.get("notify_read_format", "{comment}")
    else:
        fmt = cfg.get("read_format", "{name}。{comment}")

    if not cfg["read_username"] and not is_notify:
        # ユーザー名読み上げOFFの場合はname部分を空に
        fmt = "{comment}"

    return fmt.replace("{name}", name).replace("{comment}", message)

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

# ─── YouTube コメント取得（非公式ポーリング） ────────────────────────────────

def fetch_yt_chat(video_id: str, continuation: str = None):
    """
    YouTube Live Chat を非公式APIでポーリングして取得する。
    continuation トークンを使って続きを取得する。
    """
    if continuation:
        url = "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat"
        body = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240101.00.00",
                    "hl": "ja",
                    "gl": "JP",
                }
            },
            "continuation": continuation,
        }
        res = requests.post(url, json=body, headers=YT_HEADERS, timeout=10)
        data = res.json()
    else:
        url = f"https://www.youtube.com/watch?v={video_id}"
        res = requests.get(url, headers=YT_HEADERS, timeout=10)
        # ytInitialData から liveChatRenderer の continuation を取得
        # 複数のパターンを試す
        patterns = [
            r'"liveChatRenderer"[^}]*"continuations"\s*:\s*\[\s*\{[^}]*"continuation"\s*:\s*"([^"]+)"',
            r'"selected":true[^}]*"continuation"\s*:\s*"([^"]+)"',
            r'"reloadContinuationData"\s*:\s*\{[^}]*"continuation"\s*:\s*"([^"]+)"',
            r'"invalidationContinuationData"\s*:\s*\{[^}]*"continuation"\s*:\s*"([^"]+)"',
            r'"timedContinuationData"\s*:\s*\{[^}]*"continuation"\s*:\s*"([^"]+)"',
        ]
        for pat in patterns:
            m = re.search(pat, res.text)
            if m:
                return [], m.group(1)
        # フォールバック: liveChatRenderer周辺だけ抜き出してcontinuationを探す
        m = re.search(r'"liveChatRenderer":\{"continuations":\[(\{.{50,2000}?\})\]', res.text)
        if m:
            try:
                import json as _json
                cont_obj = _json.loads("[" + m.group(1) + "]")
                for c in cont_obj:
                    for v in c.values():
                        if isinstance(v, dict) and "continuation" in v:
                            return [], v["continuation"]
            except Exception as e:
                print(f"[YT] liveChatRenderer パースエラー: {e}")
        # 最終手段: "continuation" の値を全部拾って長いものを使う
        all_conts = re.findall(r'"continuation"\s*:\s*"([A-Za-z0-9_\-]{50,})"', res.text)
        if all_conts:
            # 最も長いものがlivechat用の可能性が高い
            best = max(all_conts, key=len)
            return [], best
        print("[YT] continuationトークンが見つかりません")
        return [], None

    # レスポンスからメッセージとnext continuationを抽出
    messages = []
    try:
        actions = (
            data.get("continuationContents", {})
                .get("liveChatContinuation", {})
                .get("actions", [])
        )
        for action in actions:
            item = (
                action.get("addChatItemAction", {})
                      .get("item", {})
                      .get("liveChatTextMessageRenderer", {})
            )
            if not item:
                continue
            name = item.get("authorName", {}).get("simpleText", "")
            runs = item.get("message", {}).get("runs", [])
            text = "".join(r.get("text", "") for r in runs)
            msg_id = item.get("id", "")
            if name and text:
                messages.append((msg_id, name, text))

        # next continuation
        conts = (
            data.get("continuationContents", {})
                .get("liveChatContinuation", {})
                .get("continuations", [{}])
        )
        next_cont = None
        for c in conts:
            next_cont = (
                c.get("invalidationContinuationData", {}).get("continuation") or
                c.get("timedContinuationData", {}).get("continuation") or
                c.get("liveChatReplayContinuationData", {}).get("continuation")
            )
            if next_cont:
                break
    except Exception as e:
        print(f"[YT] パースエラー: {e}")

    return messages, next_cont


def yt_worker():
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
        continuation = None

        try:
            # 初回: continuation トークンを取得
            _, continuation = fetch_yt_chat(video_id)
            if not continuation:
                print("[YT] continuation 取得失敗。30秒後に再試行...")
                time.sleep(30)
                continue

            while running:
                messages, continuation = fetch_yt_chat(video_id, continuation)
                for msg_id, name, text in messages:
                    if msg_id and msg_id in seen_yt_ids:
                        continue
                    if msg_id:
                        seen_yt_ids.add(msg_id)
                    if not is_blocked(name) and not is_comment_blocked(text):
                        tts_queue.put(build_read_text(name, text))
                if not continuation:
                    print("[YT] 配信終了または切断")
                    break
                time.sleep(3)

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
            ws.send("CAP REQ :twitch.tv/tags")  # display-name などのタグを受け取る
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
                            # IRCv3タグを取得（タグ部分は先頭 @ から最初の " :" まで）
                            tags = {}
                            if line.startswith("@"):
                                tag_end = line.index(" :")
                                tags_part = line[1:tag_end]
                                tags = dict(
                                    t.split("=", 1) for t in tags_part.split(";") if "=" in t
                                )
                            # display-name（ニックネーム）、なければlogin（アカウント名）
                            display_name = tags.get("display-name", "").strip()
                            login_name   = tags.get("login", "").strip()
                            if not login_name:
                                # タグにloginがない場合はIRCのnickから取得
                                irc_part = line[line.index(" :") + 2:]
                                login_name = irc_part.split("!")[0]
                            name = display_name if display_name else login_name
                            message = line.split("PRIVMSG")[1].split(":", 1)[1].strip()
                            if message.startswith("!"):
                                continue
                            # 除外リストはアカウント名・ニックネーム両方チェック
                            if not is_blocked(login_name) and not is_blocked(display_name) and not is_comment_blocked(message):
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
        "              NVIDIA Broadcast 使用時は「NVIDIA Broadcast」等で部分一致検索。\n"
        "【YouTube】チャンネル URL を設定するだけで配信開始を自動検出。\n"
        "【Twitch】OAuth: https://twitchapps.com/tmi/ で取得。\n\n"
        "pip install websocket-client requests sounddevice numpy"
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
    obs.obs_data_set_default_bool  (settings, "comment_len_limit_enabled", cfg["comment_len_limit_enabled"])
    obs.obs_data_set_default_int   (settings, "comment_len_limit",  cfg["comment_len_limit"])
    obs.obs_data_set_default_bool  (settings, "name_len_limit_enabled",    cfg["name_len_limit_enabled"])
    obs.obs_data_set_default_int   (settings, "name_len_limit",     cfg["name_len_limit"])
    obs.obs_data_set_default_bool  (settings, "exclude_emoji",      cfg["exclude_emoji"])
    obs.obs_data_set_default_bool  (settings, "exclude_yt_stamp",   cfg["exclude_yt_stamp"])
    obs.obs_data_set_default_bool  (settings, "exclude_gift",       cfg["exclude_gift"])
    obs.obs_data_set_default_bool  (settings, "emoji_image_limit_enabled", cfg["emoji_image_limit_enabled"])
    obs.obs_data_set_default_int   (settings, "emoji_image_limit",  cfg["emoji_image_limit"])
    obs.obs_data_set_default_bool  (settings, "read_system_message",cfg["read_system_message"])
    obs.obs_data_set_default_string(settings, "block_comments",     cfg["block_comments"])
    obs.obs_data_set_default_string(settings, "read_format",        cfg["read_format"])
    obs.obs_data_set_default_string(settings, "notify_read_format", cfg["notify_read_format"])
    obs.obs_data_set_default_bool  (settings, "yt_enabled",        cfg["yt_enabled"])
    obs.obs_data_set_default_string(settings, "yt_channel_url",    "")
    obs.obs_data_set_default_bool  (settings, "tw_enabled",        cfg["tw_enabled"])
    obs.obs_data_set_default_string(settings, "tw_channel",        "")
    obs.obs_data_set_default_string(settings, "tw_nick",           "")
    obs.obs_data_set_default_string(settings, "tw_oauth",          "")


def script_properties():
    props = obs.obs_properties_create()

    obs.obs_properties_add_text  (props, "voicevox_url",     "VOICEVOX URL",              obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text  (props, "voicevox_engine",  "VOICEVOX エンジン exe パス", obs.OBS_TEXT_DEFAULT)

    # スピーカーをリスト選択（主要キャラ + スタイル）
    sp = obs.obs_properties_add_list(
        props, "speaker_id", "スピーカー（キャラ／スタイル）",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_INT,
    )
    _speakers = [
        # ずんだもん
        ( 3, "ずんだもん　ノーマル"),
        ( 1, "ずんだもん　あまあま"),
        ( 7, "ずんだもん　ツンツン"),
        ( 5, "ずんだもん　セクシー"),
        (22, "ずんだもん　ささやき"),
        (38, "ずんだもん　ヒソヒソ"),
        (75, "ずんだもん　ヘロヘロ"),
        (76, "ずんだもん　なみだめ"),
        # 四国めたん
        ( 2, "四国めたん　ノーマル"),
        ( 0, "四国めたん　あまあま"),
        ( 6, "四国めたん　ツンツン"),
        ( 4, "四国めたん　セクシー"),
        (36, "四国めたん　ささやき"),
        (37, "四国めたん　ヒソヒソ"),
        # 春日部つむぎ
        ( 8, "春日部つむぎ　ノーマル"),
        # 雨晴はう
        (10, "雨晴はう　ノーマル"),
        # 波音リツ
        ( 9, "波音リツ　ノーマル"),
        (65, "波音リツ　クイーン"),
        # 玄野武宏
        (11, "玄野武宏　ノーマル"),
        (39, "玄野武宏　喜び"),
        (40, "玄野武宏　ツンギレ"),
        (41, "玄野武宏　悲しみ"),
        # 白上虎太郎
        (12, "白上虎太郎　ふつう"),
        (32, "白上虎太郎　わーい"),
        (33, "白上虎太郎　びくびく"),
        (34, "白上虎太郎　おこ"),
        (35, "白上虎太郎　びえーん"),
        # 青山龍星
        (13, "青山龍星　ノーマル"),
        (81, "青山龍星　熱血"),
        (82, "青山龍星　不機嫌"),
        (83, "青山龍星　喜び"),
        (84, "青山龍星　しっとり"),
        (85, "青山龍星　かなしみ"),
        (86, "青山龍星　囁き"),
        # 冥鳴ひまり
        (14, "冥鳴ひまり　ノーマル"),
        # 九州そら
        (16, "九州そら　ノーマル"),
        (15, "九州そら　あまあま"),
        (17, "九州そら　ツンツン"),
        (18, "九州そら　セクシー"),
        (19, "九州そら　ささやき"),
    ]
    for sid, label in _speakers:
        obs.obs_property_list_add_int(sp, label, sid)

    obs.obs_properties_add_bool  (props, "read_username",    "ユーザー名を読み上げる")
    obs.obs_properties_add_text  (props, "audio_device",     "音声出力デバイス名（部分一致）",  obs.OBS_TEXT_DEFAULT)

    obs.obs_properties_add_float_slider(props, "volume_scale",    "ボリューム",        0.0,  2.0,  0.05)
    obs.obs_properties_add_float_slider(props, "speed_scale",     "読み上げ速度",      0.5,  2.0,  0.05)
    obs.obs_properties_add_float_slider(props, "pitch_scale",     "ピッチ",           -0.15, 0.15, 0.01)
    obs.obs_properties_add_float_slider(props, "intonation_scale","イントネーション",   0.0,  2.0,  0.05)
    obs.obs_properties_add_float_slider(props, "pre_phoneme_len", "息継ぎ（前）",      0.0,  1.5,  0.05)
    obs.obs_properties_add_float_slider(props, "post_phoneme_len","息継ぎ（後）",      0.0,  1.5,  0.05)

    # ── フィルタ設定 ──────────────────────────────────────────────────────────
    obs.obs_properties_add_bool  (props, "comment_len_limit_enabled", "コメント読み上げ文字数制限")
    obs.obs_properties_add_int   (props, "comment_len_limit",    "　コメント最大文字数",      1, 500, 1)
    obs.obs_properties_add_bool  (props, "name_len_limit_enabled",    "名前読み上げ文字数制限")
    obs.obs_properties_add_int   (props, "name_len_limit",       "　名前最大文字数",          1, 100, 1)

    obs.obs_properties_add_bool  (props, "exclude_emoji",        "絵文字を除外する")
    obs.obs_properties_add_bool  (props, "exclude_yt_stamp",     "カスタムスタンプを読み上げない（YouTube）")
    obs.obs_properties_add_bool  (props, "exclude_gift",         "ギフト画像を読み上げない")
    obs.obs_properties_add_bool  (props, "emoji_image_limit_enabled", "絵文字/画像数制限")
    obs.obs_properties_add_int   (props, "emoji_image_limit",    "　絵文字/画像 上限数",       0, 50, 1)
    obs.obs_properties_add_bool  (props, "read_system_message",  "システムメッセージの読み上げ")

    obs.obs_properties_add_text  (props, "block_users",
                                   "読み上げ除外ユーザー（カンマ区切り）",
                                   obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text  (props, "block_comments",
                                   "読み上げ除外（フレーズ・行区切り）",
                                   obs.OBS_TEXT_MULTILINE)

    # ── 読み上げフォーマット ──────────────────────────────────────────────────
    obs.obs_properties_add_text  (props, "read_format",
                                   "読み上げフォーマット  {name} {comment} が使えます",
                                   obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text  (props, "notify_read_format",
                                   "通知メッセージ読み上げフォーマット  {comment} が使えます",
                                   obs.OBS_TEXT_DEFAULT)

    # ── YouTube ───────────────────────────────────────────────────────────────
    obs.obs_properties_add_bool  (props, "yt_enabled",       "YouTube 有効")
    obs.obs_properties_add_text  (props, "yt_channel_url",   "YouTube チャンネル URL",     obs.OBS_TEXT_DEFAULT)

    # ── Twitch ────────────────────────────────────────────────────────────────
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

    # 文字数制限
    cfg["comment_len_limit_enabled"] = obs.obs_data_get_bool(settings, "comment_len_limit_enabled")
    cfg["comment_len_limit"]         = obs.obs_data_get_int (settings, "comment_len_limit")
    cfg["name_len_limit_enabled"]    = obs.obs_data_get_bool(settings, "name_len_limit_enabled")
    cfg["name_len_limit"]            = obs.obs_data_get_int (settings, "name_len_limit")

    # フィルタオプション
    cfg["exclude_emoji"]             = obs.obs_data_get_bool  (settings, "exclude_emoji")
    cfg["exclude_yt_stamp"]          = obs.obs_data_get_bool  (settings, "exclude_yt_stamp")
    cfg["exclude_gift"]              = obs.obs_data_get_bool  (settings, "exclude_gift")
    cfg["emoji_image_limit_enabled"] = obs.obs_data_get_bool  (settings, "emoji_image_limit_enabled")
    cfg["emoji_image_limit"]         = obs.obs_data_get_int   (settings, "emoji_image_limit")
    cfg["read_system_message"]       = obs.obs_data_get_bool  (settings, "read_system_message")

    # 読み上げフォーマット
    cfg["block_comments"]            = obs.obs_data_get_string(settings, "block_comments")
    cfg["read_format"]               = obs.obs_data_get_string(settings, "read_format") or "{name}。{comment}"
    cfg["notify_read_format"]        = obs.obs_data_get_string(settings, "notify_read_format") or "{comment}"

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
