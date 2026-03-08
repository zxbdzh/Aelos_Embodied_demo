import os
import sys
import time
import json
import base64
import wave
import array
import subprocess
import uuid
import tempfile
from dotenv import load_dotenv
from openai import OpenAI
from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.asr.v20190614 import asr_client, models
from tencentcloud.tts.v20190823 import tts_client, models as tts_models

# 加载环境变量
load_dotenv()

# ====================== 核心配置（从环境变量读取）======================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_LLM_MODEL = os.getenv("DEEPSEEK_LLM_MODEL", "deepseek-chat")

TENCENT_SECRET_ID = os.getenv("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.getenv("TENCENT_SECRET_KEY")
TENCENT_REGION = os.getenv("TENCENT_REGION", "ap-beijing")

VOLUME_THRESHOLD = int(os.getenv("VOLUME_THRESHOLD", "800"))
RECORD_CMD = os.getenv("RECORD_CMD", "arecord -D hw:0,0 -f S16_LE -r 44100 -c 1 -d {duration} {save_path}")
PLAY_WAV_CMD = os.getenv("PLAY_WAV_CMD", "aplay -q {file_path}")

TTS_MAX_TEXT_LEN = int(os.getenv("TTS_MAX_TEXT_LEN", "100"))

# ====================== 机器人配置类 ======================
class RobotConfig:
    """运行时可调整的机器人参数"""
    
    # 腾讯云TTS支持的音色
    VOICE_OPTIONS = {
        "智瑜": 101001,    # 女声
        "智聆": 101002,    # 女声
        "智美": 101003,    # 女声
        "希希": 101004,    # 女声
        "智强": 101006,    # 男声
        "智芸": 101007,    # 女声
        "智华": 101008,    # 男声
        "智辉": 101010,    # 男声
    }
    
    def __init__(self):
        self.tts_volume = int(os.getenv("TTS_VOLUME", "5"))  # TTS音量 0-10
        self.tts_speed = int(os.getenv("TTS_SPEED", "0"))    # TTS语速 -2 到 2
        self.voice_type = int(os.getenv("TTS_VOICE_TYPE", "101001"))  # 音色ID
        self.play_volume = int(os.getenv("PLAY_VOLUME", "100"))  # 播放音量百分比
        
    def set_volume(self, volume):
        """设置TTS音量 (0-10)"""
        self.tts_volume = max(0, min(10, volume))
        print(f"🔊 TTS音量已设置为: {self.tts_volume}")
        
    def set_speed(self, speed):
        """设置TTS语速 (-2 到 2)"""
        self.tts_speed = max(-2, min(2, speed))
        print(f"⚡ TTS语速已设置为: {self.tts_speed}")
        
    def set_voice(self, voice_name):
        """通过音色名称设置音色"""
        if voice_name in self.VOICE_OPTIONS:
            self.voice_type = self.VOICE_OPTIONS[voice_name]
            print(f"🎤 音色已设置为: {voice_name}")
            return True
        return False
    
    def set_voice_by_id(self, voice_id):
        """通过音色ID设置音色"""
        if voice_id in self.VOICE_OPTIONS.values():
            self.voice_type = voice_id
            name = [k for k, v in self.VOICE_OPTIONS.items() if v == voice_id][0]
            print(f"🎤 音色已设置为: {name}")
            return True
        return False
    
    def set_play_volume(self, volume):
        """设置播放音量 (0-100)"""
        self.play_volume = max(0, min(100, volume))
        print(f"📢 播放音量已设置为: {self.play_volume}%")
        
    def get_voice_list(self):
        """获取所有可用音色"""
        return list(self.VOICE_OPTIONS.keys())

# ====================== 工具函数 ======================
def clean_tts_text(text):
    """仅保留纯中文"""
    if not text:
        return "你好呀"
    text = text.strip().replace("\n", "").replace("\t", "").replace("  ", " ")
    clean_text = ""
    for c in text:
        if '\u4e00' <= c <= '\u9fff':
            clean_text += c
    if len(clean_text) > TTS_MAX_TEXT_LEN:
        clean_text = clean_text[:TTS_MAX_TEXT_LEN]
    return clean_text if clean_text else "你好呀"

def check_audio_volume(audio_path, threshold):
    try:
        with wave.open(audio_path, 'rb') as wf:
            audio_data = wf.readframes(wf.getnframes())
            audio_array = array.array('h', audio_data)
            max_vol = max(abs(x) for x in audio_array)
            return max_vol > threshold
    except:
        return False

def install_dependencies():
    """自动安装ffmpeg（MP3转WAV需要）和aplay（一般自带）"""
    if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0:
        print("⚠️ 安装ffmpeg（MP3转WAV依赖）...")
        subprocess.run(["sudo", "apt", "update", "-y"], stdout=subprocess.DEVNULL)
        subprocess.run(["sudo", "apt", "install", "-y", "ffmpeg"], stdout=subprocess.DEVNULL)
    if subprocess.run(["which", "aplay"], capture_output=True).returncode != 0:
        print("⚠️ 安装alsa-utils（aplay依赖）...")
        subprocess.run(["sudo", "apt", "install", "-y", "alsa-utils"], stdout=subprocess.DEVNULL)

# ====================== 腾讯云ASR+TTS工具类 ======================
class VoiceTool:
    def __init__(self, config):
        self.config = config
        install_dependencies()
        
        try:
            self.cred = credential.Credential(TENCENT_SECRET_ID, TENCENT_SECRET_KEY)
            self.asr_client = asr_client.AsrClient(self.cred, TENCENT_REGION)
            self.tts_client = tts_client.TtsClient(self.cred, TENCENT_REGION)
            print("✅ 腾讯云ASR+TTS初始化成功")
        except TencentCloudSDKException as e:
            print(f"❌ 腾讯云初始化失败：{e}")
            self.cred = None

    def record_and_recognize(self, duration=3):
        save_path = "recording.wav"
        record_cmd = RECORD_CMD.format(duration=duration, save_path=save_path)
        print(f"\n🎙️ 录音中（{duration}秒）...")
        subprocess.run(record_cmd.split(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if not os.path.exists(save_path) or not check_audio_volume(save_path, VOLUME_THRESHOLD):
            if os.path.exists(save_path):
                os.remove(save_path)
            return "SILENCE"
        
        try:
            with open(save_path, "rb") as f:
                audio_base64 = base64.b64encode(f.read()).decode("utf-8")
            req = models.SentenceRecognitionRequest()
            req.EngSerViceType = "16k_zh"
            req.VoiceFormat = "wav"
            req.Data = audio_base64
            req.SourceType = 1
            resp = self.asr_client.SentenceRecognition(req)
            os.remove(save_path)
            return resp.Result.strip() if resp.Result else None
        except TencentCloudSDKException as e:
            print(f"❌ ASR识别失败：{e}")
            os.remove(save_path)
            return None

    def tts_and_play(self, text):
        if not self.cred:
            print("❌ 腾讯云未初始化")
            return
        
        clean_text = clean_tts_text(text)
        print(f"📝 纯中文合成文本：{clean_text}")
        
        try:
            req = tts_models.TextToVoiceRequest()
            req.Text = clean_text
            req.VoiceType = self.config.voice_type  # 使用配置中的音色
            req.Codec = "mp3"
            req.SessionId = str(uuid.uuid4()).replace("-", "")[:16]
            req.Speed = self.config.tts_speed      # 使用配置中的语速
            req.Volume = self.config.tts_volume    # 使用配置中的音量
            
            resp = self.tts_client.TextToVoice(req)
            audio_base64 = resp.Audio
            if not audio_base64:
                print("❌ TTS合成返回空音频")
                return
        except TencentCloudSDKException as e:
            print(f"❌ TTS合成失败：{e}")
            return
        
        try:
            tmp_mp3 = tempfile.mktemp(suffix=".mp3")
            with open(tmp_mp3, "wb") as f:
                f.write(base64.b64decode(audio_base64))
            
            tmp_wav = tempfile.mktemp(suffix=".wav")
            ffmpeg_cmd = f"ffmpeg -i {tmp_mp3} -y -ac 1 -ar 44100 {tmp_wav}"
            subprocess.run(ffmpeg_cmd.split(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            print(f"🔊 播放：{clean_text}")
            # 使用 ffmpeg 控制音量，然后播放
            volume_ratio = self.config.play_volume / 100.0
            tmp_wav_adj = tempfile.mktemp(suffix=".wav")
            ffmpeg_vol_cmd = f"ffmpeg -i {tmp_wav} -y -af volume={volume_ratio} {tmp_wav_adj}"
            subprocess.run(ffmpeg_vol_cmd.split(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # 播放调整音量后的音频
            subprocess.run(PLAY_WAV_CMD.format(file_path=tmp_wav_adj).split(), stdout=subprocess.DEVNULL)
            
            # 清理调整后的临时文件
            if os.path.exists(tmp_wav_adj):
                os.remove(tmp_wav_adj)
            
            os.remove(tmp_mp3)
            os.remove(tmp_wav)
        except Exception as e:
            print(f"❌ 播放失败：{e}")
            if os.path.exists(tmp_mp3):
                os.remove(tmp_mp3)
            if os.path.exists(tmp_wav):
                os.remove(tmp_wav)

# ====================== LLM客户端 ======================
class LLMClient:
    def __init__(self, config):
        self.config = config
        self.client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    def get_llm_response(self, user_text):
        voice_list = ", ".join(self.config.get_voice_list())
        
        prompt = f"""
你是一个智能语音助手。根据用户输入，返回JSON格式的响应。

当前配置：
- TTS音量: {self.config.tts_volume} (范围0-10)
- TTS语速: {self.config.tts_speed} (范围-2到2)  
- 音色ID: {self.config.voice_type}
- 播放音量: {self.config.play_volume}% (范围0-100)

可用音色: {voice_list}

返回格式：
{{"response": "回复内容", "config": {{"volume": 数字, "speed": 数字, "voice": "音色名", "play_volume": 数字}}}}

config字段可选，仅当用户要求修改参数时才包含。response字段必填，仅用中文汉字（无标点），≤50字。

示例：
用户: "把音量调大一点" -> {{"response": "好的音量已调大", "config": {{"volume": 8}}}}
用户: "换成男声" -> {{"response": "好的已切换为男声", "config": {{"voice": "智强"}}}}
用户: "你好" -> {{"response": "你好很高兴见到你"}}
用户输入：{user_text}
"""
        try:
            completion = self.client.chat.completions.create(
                model=DEEPSEEK_LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=200
            )
            return completion.choices[0].message.content.strip()
        except Exception as e:
            print(f"❌ LLM调用失败：{e}")
            return '{"response": "抱歉我没听清"}'

# ====================== 主函数 ======================
def main():
    config = RobotConfig()
    voice_tool = VoiceTool(config)
    llm_client = LLMClient(config)
    
    print("\n" + "="*50)
    print("🚀 语音对话系统启动")
    print(f"当前音色ID: {config.voice_type}")
    print(f"TTS音量: {config.tts_volume}, 语速: {config.tts_speed}")
    print(f"播放音量: {config.play_volume}%")
    print("可用音色:", ", ".join(config.get_voice_list()))
    print("按Ctrl+C退出")
    print("="*50 + "\n")
    
    try:
        while True:
            user_text = voice_tool.record_and_recognize(duration=3)
            if user_text == "SILENCE" or not user_text:
                time.sleep(0.5)
                continue
            print(f"✅ 识别结果：{user_text}")
            
            llm_raw = llm_client.get_llm_response(user_text)
            print(f"📝 LLM返回：{llm_raw}")
            
            try:
                if "```json" in llm_raw:
                    llm_raw = llm_raw.split("```json")[1].split("```")[0].strip()
                resp_dict = json.loads(llm_raw)
                tts_text = resp_dict.get("response", "你好呀")
                
                # 处理配置修改
                if "config" in resp_dict:
                    cfg = resp_dict["config"]
                    if "volume" in cfg:
                        config.set_volume(cfg["volume"])
                    if "speed" in cfg:
                        config.set_speed(cfg["speed"])
                    if "voice" in cfg:
                        config.set_voice(cfg["voice"])
                    if "play_volume" in cfg:
                        config.set_play_volume(cfg["play_volume"])
                
                voice_tool.tts_and_play(tts_text)
                    
            except json.JSONDecodeError:
                print("⚠️ JSON解析失败，播放默认回复")
                voice_tool.tts_and_play("抱歉我没理解你的意思")
            except Exception as e:
                print(f"❌ 处理异常：{e}")
            
            print("-" * 40)
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n🛑 程序终止")

if __name__ == "__main__":
    main()
