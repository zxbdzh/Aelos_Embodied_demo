# 智能语音对话系统

基于 DeepSeek LLM 和腾讯云 ASR/TTS 的智能语音对话系统，支持语音交互、参数调节和智能退出。

## 功能特性

- 🎙️ **语音识别**：腾讯云 ASR 实时语音转文字
- 🔊 **语音合成**：腾讯云 TTS 文字转语音，支持多种音色
- 🤖 **智能对话**：DeepSeek LLM 驱动的自然语言理解
- ⚙️ **运行时配置**：通过语音动态调整音量、语速、音色
- 💾 **配置记忆**：自动保存配置到 .env 文件
- 🚪 **智能退出**：说"再见"自动退出程序

## 环境要求

- Python 3.8+
- ffmpeg（音频处理）
- alsa-utils（录音播放）

## 安装

```bash
# 安装 Python 依赖
pip install python-dotenv openai tencentcloud-sdk-python

# 安装系统依赖（Ubuntu）
sudo apt install ffmpeg alsa-utils
```

## 配置

复制 `.env.example` 为 `.env` 并填写配置：

```bash
cp .env.example .env
```

### 配置项说明

| 配置项             | 说明              | 范围/默认值                 |
| ------------------ | ----------------- | --------------------------- |
| DEEPSEEK_API_KEY   | DeepSeek API 密钥 | -                           |
| DEEPSEEK_BASE_URL  | DeepSeek API 地址 | https://api.deepseek.com/v1 |
| DEEPSEEK_LLM_MODEL | 使用的模型        | deepseek-chat               |
| TENCENT_SECRET_ID  | 腾讯云 SecretId   | -                           |
| TENCENT_SECRET_KEY | 腾讯云 SecretKey  | -                           |
| TENCENT_REGION     | 腾讯云区域        | ap-beijing                  |
| TTS_VOICE_TYPE     | 音色ID            | 101001                      |
| TTS_SPEED          | 语速              | -2 到 2                     |
| PLAY_VOLUME        | 播放音量          | 0-100                       |

### 可用音色

| 音色ID | 名称 | 性别 |
| ------ | ---- | ---- |
| 101001 | 智瑜 | 女声 |
| 101002 | 智聆 | 女声 |
| 101003 | 智美 | 女声 |
| 101004 | 希希 | 女声 |
| 101006 | 智强 | 男声 |
| 101007 | 智芸 | 女声 |
| 101008 | 智华 | 男声 |
| 101010 | 智辉 | 男声 |

## 使用

```bash
python deepseek_speak.py
```

## 语音指令示例

- "你好" → 正常对话
- "声音调大一点" → 音量 +20%
- "声音小一点" → 音量 -20%
- "换成男声" → 切换为男声音色
- "语速快一点" → 调快语速
- "再见" / "拜拜" → 退出程序

## 项目结构

```
.
├── deepseek_speak.py   # 主程序
├── .env                # 配置文件（需自行创建）
├── .env.example        # 配置示例
├── .gitignore          # Git 忽略规则
└── README.md           # 说明文档
```

## 许可证

MIT License
