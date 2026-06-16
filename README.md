# obs-comment-voicevox

YouTube Live / Twitch のコメントを **VOICEVOX** で読み上げる OBS スクリプトです。

## 機能

- YouTube Live・Twitch のコメントをリアルタイム取得して VOICEVOX で音声合成
- VOICEVOX エンジンの自動起動・自動終了
- sounddevice による任意オーディオデバイスへの出力（VB-Audio CABLE / NVIDIA Broadcast 等）
- スピーカーをドロップダウンで選択（ずんだもん・四国めたんほか主要キャラ全スタイル収録）
- ボリューム・速度・ピッチ・イントネーション・息継ぎをスライダーで調整
- コメント・名前の読み上げ文字数制限
- 読み上げ除外ユーザー・除外フレーズの設定
- 絵文字除外・カスタムスタンプ除外・システムメッセージ読み上げのオン/オフ
- 読み上げフォーマットのカスタマイズ（`{name}` / `{comment}` プレースホルダー対応）
- YouTube: チャンネル URL を設定するだけで配信開始を自動検出・再接続
- Twitch: IRC over WebSocket、OAuth トークン認証、`!` コマンドの自動除外

## 動作要件

- OBS Studio（Python スクリプト機能対応バージョン）
- [VOICEVOX](https://voicevox.hiroshiba.jp/)（エンジン同梱の製品版）
- Python 依存ライブラリ

```
pip install websocket-client requests sounddevice numpy
```

## インストール

1. [Releases](https://github.com/asaitetsuya-oss/obs-comment-voicevox/releases) から `comment_reader.py` をダウンロード
2. OBS Studio → ツール → スクリプト → `+` ボタンで追加
3. 設定画面で VOICEVOX エンジンの exe パスを入力
4. YouTube チャンネル URL または Twitch チャンネル名・OAuth を入力して完了

## 設定項目

### 基本

| 項目 | 説明 | デフォルト |
|------|------|-----------|
| VOICEVOX URL | エンジンの API エンドポイント | `http://localhost:50021` |
| VOICEVOX エンジン exe パス | エンジンの実行ファイルパス | — |
| スピーカー | キャラクター・スタイルの選択 | ずんだもん ノーマル |
| 音声出力デバイス名 | 部分一致で検索（例: `CABLE Input`） | `CABLE Input` |
| ユーザー名を読み上げる | オン/オフ | オン |

### 音声パラメータ

| 項目 | 範囲 | デフォルト |
|------|------|-----------|
| ボリューム | 0.0 〜 2.0 | 1.0 |
| 読み上げ速度 | 0.5 〜 2.0 | 1.0 |
| ピッチ | -0.15 〜 0.15 | 0.0 |
| イントネーション | 0.0 〜 2.0 | 1.0 |
| 息継ぎ（前） | 0.0 〜 1.5 | 1.0 |
| 息継ぎ（後） | 0.0 〜 1.5 | 1.0 |

### フィルタ

| 項目 | 説明 | デフォルト |
|------|------|-----------|
| コメント読み上げ文字数制限 | 超過分をカット | オン / 50文字 |
| 名前読み上げ文字数制限 | 超過分をカット | オン / 10文字 |
| 絵文字を除外する | Unicode 絵文字を除去 | オフ |
| カスタムスタンプを読み上げない | YouTube スタンプ | オフ |
| ギフト画像を読み上げない | — | オフ |
| 絵文字/画像数制限 | 上限を超えたコメントをスキップ | オフ / 5個 |
| システムメッセージの読み上げ | 入退室通知等 | オフ |
| 読み上げ除外ユーザー | カンマ区切りで指定 | — |
| 読み上げ除外フレーズ | 行区切りで指定 | — |

### 読み上げフォーマット

| 項目 | 説明 | デフォルト |
|------|------|-----------|
| 読み上げフォーマット | `{name}` / `{comment}` が使えます | `{name}。{comment}` |
| 通知メッセージフォーマット | `{comment}` が使えます | `{comment}` |

### YouTube

チャンネル URL（例: `https://www.youtube.com/@your_channel`）を設定するだけで、配信開始を自動検出します。配信が終了または切断された場合は 30 秒後に再接続を試みます。

### Twitch

OAuth トークンは [https://twitchapps.com/tmi/](https://twitchapps.com/tmi/) で取得してください。`!` から始まるコメントは自動的に除外されます。

## クレジット

音声合成に [VOICEVOX](https://voicevox.hiroshiba.jp/) を使用しています。  
VOICEVOX の利用規約に従い、配信・動画にクレジットの記載をお願いします。

例: `VOICEVOX:ずんだもん`

## ライセンス

MIT
