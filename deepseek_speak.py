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

ACTION_DIC = {"stand": "站立", "walk": "行走", "wave": "挥手", "sit": "坐下", "none": "无"}
ACTION_STRATEGY = os.getenv("ACTION_STRATEGY", "true").lower() == "true"
VOLUME_THRESHOLD = int(os.getenv("VOLUME_THRESHOLD", "800"))
RECORD_CMD = os.getenv("RECORD_CMD", "arecord -D hw:0,0 -f S16_LE -r 44100 -c 1 -d {duration} {save_path}")
PLAY_WAV_CMD = os.getenv("PLAY_WAV_CMD", "aplay -q {file_path}")

# 腾讯云TTS配置
TTS_VOICE_TYPE = int(os.getenv("TTS_VOICE_TYPE", "101001"))
TTS_MAX_TEXT_LEN = int(os.getenv("TTS_MAX_TEXT_LEN", "100"))

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
    # 检查ffmpeg
    if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0:
        print("⚠️ 安装ffmpeg（MP3转WAV依赖）...")
        subprocess.run(["sudo", "apt", "update", "-y"], stdout=subprocess.DEVNULL)
        subprocess.run(["sudo", "apt", "install", "-y", "ffmpeg"], stdout=subprocess.DEVNULL)
    # 检查aplay（alsa-utils包含aplay）
    if subprocess.run(["which", "aplay"], capture_output=True).returncode != 0:
        print("⚠️ 安装alsa-utils（aplay依赖）...")
        subprocess.run(["sudo", "apt", "install", "-y", "alsa-utils"], stdout=subprocess.DEVNULL)

# ====================== 腾讯云ASR+TTS工具类 ======================
class VoiceTool:
    def __init__(self):
        # 先安装依赖
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
        
        # 腾讯云TTS合成MP3（base64格式）
        try:
            req = tts_models.TextToVoiceRequest()
            req.Text = clean_text
            req.VoiceType = TTS_VOICE_TYPE
            req.Codec = "mp3"
            req.SessionId = str(uuid.uuid4()).replace("-", "")[:16]
            req.Speed = 0
            req.Volume = 0
            
            resp = self.tts_client.TextToVoice(req)
            audio_base64 = resp.Audio
            if not audio_base64:
                print("❌ TTS合成返回空音频")
                return
        except TencentCloudSDKException as e:
            print(f"❌ TTS合成失败：{e}")
            return
        
        # 核心修改：MP3转WAV + aplay播放
        try:
            # 1. 保存MP3临时文件
            tmp_mp3 = tempfile.mktemp(suffix=".mp3")
            with open(tmp_mp3, "wb") as f:
                f.write(base64.b64decode(audio_base64))
            
            # 2. 用ffmpeg将MP3转成WAV（aplay支持的格式）
            tmp_wav = tempfile.mktemp(suffix=".wav")
            ffmpeg_cmd = f"ffmpeg -i {tmp_mp3} -y -ac 1 -ar 44100 {tmp_wav}"
            subprocess.run(ffmpeg_cmd.split(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # 3. 用aplay播放WAV文件
            print(f"🔊 播放：{clean_text}")
            subprocess.run(PLAY_WAV_CMD.format(file_path=tmp_wav).split(), stdout=subprocess.DEVNULL)
            
            # 4. 清理临时文件
            os.remove(tmp_mp3)
            os.remove(tmp_wav)
        except Exception as e:
            print(f"❌ 播放失败：{e}")
            # 兜底清理
            if os.path.exists(tmp_mp3):
                os.remove(tmp_mp3)
            if os.path.exists(tmp_wav):
                os.remove(tmp_wav)

# ====================== LLM客户端 ======================
class LLMClient:
    def __init__(self):
        self.client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    def get_llm_response(self, user_text):
        prompt = f"""
        输入：{user_text}
        输出：仅返回JSON字符串，包含action和response字段
        action可选：stand/walk/wave/sit/none
        response：仅用中文汉字（无标点），≤50字
        示例：{{"action":"stand","response":"你好我现在执行站立动作"}}
        """
        try:
            completion = self.client.chat.completions.create(
                model=DEEPSEEK_LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=100
            )
            return completion.choices[0].message.content.strip()
        except Exception as e:
            print(f"❌ LLM调用失败：{e}")
            return '{"action":"none","response":"抱歉我没听清"}'

# ====================== 主函数 ======================
def main():
    voice_tool = VoiceTool()
    llm_client = LLMClient()
    
    print("\n🚀 终极版语音对话系统（aplay播放）启动，按Ctrl+C退出\n")
    
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
                action_name = resp_dict.get("action", "none")
                tts_text = resp_dict.get("response", "你好呀")
                
                voice_tool.tts_and_play(tts_text)
                
                if action_name in ACTION_DIC and ACTION_STRATEGY:
                    print(f"\n🤖 执行动作：{ACTION_DIC[action_name]}")
                    time.sleep(1)
                    
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
