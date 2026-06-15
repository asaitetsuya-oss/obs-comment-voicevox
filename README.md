# obs-comment-voicevox

YouTube Live / Twitch のコメントを **VOICEVOX** で読み上げる OBS スクリプトです。

わんコメ不要・VOICEVOX自動起動・VB-Audio対応・OBS設定画面でリアルタイムに音声調整ができます。

---

## 機能

- YouTube Live / Twitch のコメントをリアルタイム取得して読み上げ
- **わんコメ不依存**（YouTube は pytchat で非公式取得、Twitch は IRC over WebSocket）
- **VOICEVOX エンジンを OBS 起動時に自動起動・終了時に自動終了**
- VB-Audio Virtual Cable 等、任意の音声デバイスに出力
- OBS スクリプト設定画面からボイス・速度・ピッチ・イントネーション・息継ぎをリアルタイム調整
- ユーザー名の読み上げ ON/OFF
- 除外ユーザー設定（ボットや特定ユーザーをスキップ）
- YouTube チャンネル URL を設定するだけで配信開始を自動検出（Video ID 手動入力不要）

---

## 必要環境

- Windows 10/11
- OBS Studio（Python スクリプト対応版）
- Python 3.10+
- [VOICEVOX](https://voicevox.hiroshiba.jp/)
- [VB-Audio Virtual Cable](https://vb-audio.com/Cable/)（任意）

---

## インストール

**1. 依存ライブラリをインストール**

OBS が使用している Python に対して実行してください。

```powershell
pip install pytchat websocket-client requests sounddevice numpy
```

**2. スクリプトをダウンロード**

`comment_reader.py` を任意のフォルダに配置します（例: `D:/obs-script/`）。

**3. OBS に登録**

ツール → スクリプト → `+` → `comment_reader.py` を選択

---

## 設定

OBS のスクリプト設定画面で以下を入力します。

### 基本設定

| 項目 | 説明 |
|------|------|
| VOICEVOX URL | `http://localhost:50021`（デフォルトのまま） |
| VOICEVOX エンジン exe パス | `run.exe` のフルパス |
| スピーカー ID | VOICEVOX のキャラクター ID（ずんだもんノーマル=3, ツンツン=7） |
| 音声出力デバイス名 | 部分一致で検索。VB-Audio なら `CABLE Input` |
| 読み上げ最大文字数 | 長いコメントはここで切る |
| 除外ユーザー | カンマ区切りで指定（例: `nightbot,streamelements`） |

### スピーカー ID 一覧の確認方法

VOICEVOX 起動中に以下を実行：

```powershell
python -c "import requests; [print(s['name'], [(st['name'], st['id']) for st in s['styles']]) for s in requests.get('http://localhost:50021/speakers').json()]"
```

### Twitch 設定

| 項目 | 説明 |
|------|------|
| Twitch チャンネル名 | 自分のチャンネル名（例: `your_channel`） |
| Twitch ユーザー名 | 同上 |
| Twitch OAuth トークン | 下記で取得した `oauth:xxxxxxxxxx` 形式 |

**OAuth トークン取得**

1. https://id.twitch.tv/oauth2/authorize?response_type=token&client_id=q6batx0epp608isickayubi39itsckt&redirect_uri=https://twitchapps.com/tmi/&scope=chat:read にアクセス
2. 表示されたトークンを `oauth:` を付けて入力

### YouTube 設定

| 項目 | 説明 |
|------|------|
| YouTube チャンネル URL | `https://www.youtube.com/@your_channel` |

チャンネル URL を設定するだけで、配信開始を自動検出します。Video ID の手動入力は不要です。

---

## 音声パラメータ

OBS 設定画面のスライダーで調整できます。次のコメントから即座に反映されます。

| スライダー | 範囲 | デフォルト |
|-----------|------|----------|
| ボリューム | 0.0〜2.0 | 1.0 |
| 読み上げ速度 | 0.5〜2.0 | 1.0 |
| ピッチ | -0.15〜0.15 | 0.0 |
| イントネーション | 0.0〜2.0 | 1.0 |
| 息継ぎ（前） | 0.0〜1.5 | 0.1 |
| 息継ぎ（後） | 0.0〜1.5 | 0.1 |

---

## 注意事項

- YouTube のコメント取得は **pytchat（非公式）** を使用しています。YouTube 側の仕様変更で動作しなくなる場合があります
- Twitch は公式の IRC over WebSocket を使用しています
- VOICEVOX エンジンのパスは環境に合わせて設定画面から変更してください

---

## ライセンス

MIT
