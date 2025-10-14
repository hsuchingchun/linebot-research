import os
from dotenv import load_dotenv
from flask import Flask, request
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
)
from linebot.v3.messaging.models import (
    # 訊息模型 (用於發送)
    ReplyMessageRequest,
    TextMessage as TextSendMessage,
    # FlexMessage as FlexSendMessage,
    # MessageAction
)
from linebot.v3.webhooks import (
    MessageEvent, TextMessageContent,
)
from linebot.v3.webhook import WebhookParser

import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta, timezone 
import json
import re
from collections import Counter
import certifi # 引入 certifi 用於 SSL 憑證

#請確保 flex_templates 模組存在並包含以下函式：
from flex_templates import (
    create_vote_flex_message,
    create_consensus_check_message,
    create_final_selection_message
)

load_dotenv()
from prompt import ask_assistant_with_role

# 💡 實驗角色代碼與類型對應表 (依照您的要求新增)
AI_ROLE_MAPPING = {
    "A組": "混合型AI",
    "B組": "整合型AI",
    "C組": "探究型 AI",
    "D組": "無介入AI",
}

# 💡 設置實驗階段參數
CONSENSUS_CHECK_SECONDS = 30  # 30秒後發送共識檢查訊息 (暖身與正式皆適用)
WARMUP_DURATION_MINUTES = 1   # 暖身實驗時長 1 分鐘
MAIN_DURATION_MINUTES = 5     # 正式實驗時長 2 分鐘
TEAM_SIZE = 2                 # 假設團隊人數為 2 人，用於檢查最終共識

# ====== Flask 和 Firebase 初始化 ======

app = Flask(__name__)

def get_firebase_credentials_from_env():
    """從環境變數讀取 Firebase 服務帳號金鑰。"""
    firebase_credentials = os.getenv("FIREBASE_CREDENTIALS")
    if not firebase_credentials:
        return None 
    try:
        # 使用 json.loads 解析 JSON 字串
        service_account_info = json.loads(firebase_credentials)
        print("✅ 成功從環境變數讀取 Firebase 金鑰")
        return credentials.Certificate(service_account_info)
    except json.JSONDecodeError:
        raise ValueError("FIREBASE_CREDENTIALS 環境變數格式錯誤，請確保它是單行且用單引號包覆的 JSON 字串。")

if not firebase_admin._apps:
    try:
        firebase_cred = get_firebase_credentials_from_env()
        if firebase_cred:
            firebase_admin.initialize_app(firebase_cred)
            db = firestore.client()
        else:
            print("⚠️ 未找到 Firebase 憑證，Firestore 功能將無法使用。")
            db = None # 確保 db 變數存在但為 None
    except Exception as e:
        print(f"❌ Firebase 初始化失敗: {e}")
        db = None

# ====== 時間及階段輔助函數 (保持不變) ======

def get_phase_duration_minutes(phase: str) -> int:
    """根據實驗階段返回對應的時長（分鐘）。"""
    return WARMUP_DURATION_MINUTES if phase == 'warmup' else MAIN_DURATION_MINUTES

def format_duration(td: timedelta) -> str:
    """將 timedelta 格式化為 MM:SS 字串。"""
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        total_seconds = 0
    
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    
    return f"{minutes:02d}:{seconds:02d}"

def get_remaining_time_text(start_time_dt: datetime, current_phase: str) -> str:
    """計算剩餘討論時間，並返回格式化字串 (顧問風格)。"""
    # 根據當前階段獲取總時長
    total_duration = timedelta(minutes=get_phase_duration_minutes(current_phase))
    
    # 確保 start_time_dt 始終是 UTC-aware
    if start_time_dt.tzinfo is None or start_time_dt.tzinfo.utcoffset(start_time_dt) is None:
        start_time_dt = start_time_dt.replace(tzinfo=timezone.utc)
        
    elapsed_time = datetime.now(timezone.utc) - start_time_dt
    remaining_time = total_duration - elapsed_time
    
    remaining_text = format_duration(remaining_time)
    
    if remaining_time.total_seconds() <= 0:
         return "✅ 討論時間已結束"
    elif remaining_time.total_seconds() < 30:
         return f"討論剩餘 {remaining_text}"
    elif remaining_time.total_seconds() < 60:
         return f"⚠️ 討論進入倒數階段，討論時間剩餘 {remaining_text}"
    
    return f"{remaining_text}"

def get_messages_collection_name(phase: str) -> str:
    """根據實驗階段 (phase) 決定 Firestore 儲存訊息的子集合名稱。"""
    return "warmup_messages" if phase == 'warmup' else "main_messages"

def get_votes_collection_name(phase: str) -> str:
    """根據實驗階段 (phase) 決定 Firestore 儲存初始投票的子集合名稱。"""
    return "warmup_votes" if phase == 'warmup' else "main_votes"


# ====== LINE Bot 初始化與正規表達式 ======
channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
channel_secret = os.getenv("LINE_CHANNEL_SECRET")

# 使用 Configuration 和 ApiClient 初始化 MessagingApi (取代 LineBotApi)
configuration = Configuration(access_token=channel_access_token, ssl_ca_cert=certifi.where())
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client) 

# 使用 v3 webhooks 模組中的 WebhookParser
parser = WebhookParser(channel_secret)

COMMAND_PATTERN = re.compile(r"@AI顧問 開始(暖身|正式)實驗 (.+)")
VOTE_PATTERN = re.compile(r"^我選(玩桌遊|公益淨灘|包場看電影| Amy| Sally| Nancy)$")
CONSENSUS_PATTERN = re.compile(r"^(已有共識|需要再討論)$")
FINAL_VOTE_PATTERN = re.compile(r"^我們最終選擇 (【玩桌遊】|【公益淨灘】|【包場看電影】|Amy|Sally|Nancy)$")
MIN_VOTES_REQUIRED = 1

# ====== Webhook 路由入口 ======
@app.route("/callback", methods=["POST"])
def webhook():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    events = parser.parse(body, signature)

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            # 取得群組/房間 ID（相容不同欄位命名）
            source = event.source
            source_id = (
                getattr(source, 'group_id', None) or getattr(source, 'groupId', None) or
                getattr(source, 'room_id', None) or getattr(source, 'roomId', None)
            )
            if not source_id:
                return "OK"

            user_id = getattr(source, 'user_id', None) or getattr(source, 'userId', None)
            msg_text = event.message.text.strip()
            
            # 確保 Firebase 已初始化
            if db is None:
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextSendMessage(text="❌ 系統服務異常：Firebase 資料庫未成功連線。")]
                    )
                )
                return "OK"

            # 階段一：開始實驗指令
            match = COMMAND_PATTERN.match(msg_text)
            if match:
                experiment_type = match.group(1).strip()
                input_role_code = match.group(2).strip() # 獲取輸入代碼 (例如: A組)
                # 統一內部相位標籤：暖身→warmup，正式→main
                phase = 'warmup' if experiment_type == '暖身' else 'main'
                
                # 💡 根據輸入代碼查找對應的 AI 角色名稱
                bot_role = AI_ROLE_MAPPING.get(input_role_code, input_role_code)
                
                exp_doc_ref = db.collection("experiments").document(source_id)
                
                # 💡 儲存時使用 timezone.utc 確保時間戳記為 UTC-aware
                now_utc = datetime.now(timezone.utc) 
                
                # 💡 [修正 1] 新增 discussion_prompt_sent 旗標
                exp_doc_ref.set({
                    "group_id": source_id, "bot_role": bot_role, "phase": phase,  # 儲存轉換後的角色名稱
                    "start_time": now_utc, 
                    "message_count": 0, "votes_count": 0, # votes_count 僅作記錄用
                    "consensus_check_sent": False, 
                    "final_vote_sent": False,
                    "discussion_prompt_sent": False # <-- 新增旗標
                })
                
                flex_message = create_vote_flex_message(phase, bot_role)
                # 調整歡迎訊息，顯示已映射的角色名稱
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            TextSendMessage(text=f"【{experiment_type}實驗】開始\n我是你們的 AI 顧問，在過程中協助大家一起進行決策✨。"),
                            flex_message
                        ]
                    )
                )
                print(f"啟動【{phase}實驗】 組別: {input_role_code} 角色: {bot_role}")
                return "OK"

            # 取得當前實驗狀態
            exp_doc_ref = db.collection("experiments").document(source_id)
            exp_doc = exp_doc_ref.get()
            if not exp_doc.exists:
                # 顧問風格回應
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextSendMessage(text="請您先輸入指令來啟動新的實驗流程 (例如: @AI顧問 開始暖身/正式實驗 組別)。")]
                    )
                )
                return "OK"

            exp_data = exp_doc.to_dict()
            current_role = exp_data.get("bot_role")
            current_phase = exp_data.get("phase", "main")
            message_count = exp_data.get("message_count", 0)
            # 💡 [修正 2] 獲取新旗標狀態
            discussion_prompt_sent = exp_data.get("discussion_prompt_sent", False) 
            
            # 💡 取得初始投票集合參考
            vote_collection_name = get_votes_collection_name(current_phase)
            votes_collection_ref = exp_doc_ref.collection(vote_collection_name)
            
            # 💡 在進行階段檢查前，先從 Firestore 串流計算出當前準確的投票人數
            vote_docs = list(votes_collection_ref.stream())
            current_votes_count = len(vote_docs) # 使用這個準確的值進行所有後續檢查

            # 計時邏輯
            start_time_dt = exp_data.get("start_time")
            duration = timedelta(seconds=0)
            
            # 獲取當前階段的總時長
            current_duration_minutes = get_phase_duration_minutes(current_phase)
            total_duration = timedelta(minutes=current_duration_minutes)
            
            if isinstance(start_time_dt, datetime):
                try:
                    # 確保 start_time_dt 始終是 UTC-aware
                    if start_time_dt.tzinfo is None or start_time_dt.tzinfo.utcoffset(start_time_dt) is None:
                        start_time_dt = start_time_dt.replace(tzinfo=timezone.utc)
                        
                    duration = datetime.now(timezone.utc) - start_time_dt
                    
                    if duration.total_seconds() < 0:
                        duration = timedelta(seconds=0)
                        
                    # 更新計時器文字
                    print (f"⏳ 實驗已進行時間：{format_duration(duration)}")
                except Exception as e:
                    print(f"時間計算失敗: {e}")
            
            message_collection_name = get_messages_collection_name(current_phase)
            messages_collection_ref = exp_doc_ref.collection(message_collection_name)

            # ====== 階段二：共識檢查介入 (現在暖身與正式階段皆適用) ======
            if exp_data.get("consensus_check_sent") == False and \
               duration.total_seconds() >= CONSENSUS_CHECK_SECONDS and \
               current_votes_count >= TEAM_SIZE: 
                
                elapsed_time_formatted = format_duration(duration)
                
                check_message = create_consensus_check_message(elapsed_time_formatted)
                
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            check_message
                        ]
                    )
                )
                
                exp_doc_ref.update({"consensus_check_sent": True})
                # 儲存使用者訊息並返回
                # 💡 使用 UTC 時區
                messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})
                return "OK" 

            # ====== 階段三：時間到期觸發最終投票 (強制觸發，不檢查初始投票人數) ======
            if exp_data.get("final_vote_sent") == False and duration.total_seconds() >= total_duration.total_seconds():
                
                # 時間到期，強制觸發最終投票
                
                # 檢查初始投票是否完成，並在訊息中提醒
                initial_vote_status_text = ""
                if current_votes_count < TEAM_SIZE:
                    remaining_people = TEAM_SIZE - current_votes_count
                    initial_vote_status_text = f"\n⚠️ 注意：投票人數不足 ({current_votes_count}/{TEAM_SIZE})，但討論時間已結束。請團隊直接進行最終決策。"

                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            TextSendMessage(text=f"🚨 時間到！實驗的 {current_duration_minutes} 分鐘討論時間已結束，請團隊進行最終決策。{initial_vote_status_text}"),
                            create_final_selection_message(current_phase) # 使用新的 phase-aware 函式
                        ]
                    )
                )
                exp_doc_ref.update({"final_vote_sent": True})
                # 儲存使用者訊息並返回
                # 💡 使用 UTC 時區
                messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})
                return "OK"
                    
            # ====== 階段四：處理共識檢查回覆（現在可隨時手動觸發共識流程，暖身/正式皆可） ======
            consensus_match = CONSENSUS_PATTERN.match(msg_text)
            # 💡 修改：允許暖身 (warmup) 階段也能處理共識
            if (current_phase == 'main' or current_phase == 'warmup') and consensus_match and exp_data.get("final_vote_sent") == False:
                choice = consensus_match.group(1).strip() 
                
                consensus_votes_ref = exp_doc_ref.collection("consensus_votes")
                # 💡 優化：這裡也建議使用 datetime.now(timezone.utc) 
                consensus_votes_ref.document(user_id).set({
                    "user_id": user_id, 
                    "choice": choice, 
                    "timestamp": datetime.now(timezone.utc).isoformat() # 統一使用 UTC 時區
                })
                
                # 重新獲取所有共識投票
                vote_docs_consensus = list(consensus_votes_ref.stream())
                
                if len(vote_docs_consensus) >= TEAM_SIZE:
                    all_consensus = all(doc.to_dict().get("choice") == "已有共識" for doc in vote_docs_consensus)
                    
                    if all_consensus:
                        # 4-1: 如果所有人都說「已有共識」，則發送最終投票 (顧問風格回應)
                        # 這就是發送您詢問的訊息的地方
                        line_bot_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[
                                    TextSendMessage(text="太棒了！團隊一致確認已達成共識，現在請大家進行最終選擇。"),
                                    create_final_selection_message(current_phase) # 💡 使用新的 phase-aware 函式
                                ]
                            )
                        )
                        exp_doc_ref.update({"final_vote_sent": True})
                    else:
                        # 4-2: 如果有人說「需要再討論」，則提醒繼續 (顧問風格回應)
                        line_bot_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextSendMessage(text=f"目前看來部分成員建議繼續討論。我們仍有充裕的時間，還剩 {get_remaining_time_text(start_time_dt, current_phase)}，請繼續交流意見。")]
                            )
                        )
                else:
                    # 顧問風格回應
                    remaining_consensus_voters = TEAM_SIZE - len(vote_docs_consensus)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextSendMessage(text=f"目前有 {len(vote_docs_consensus)} 位成員已確認共識，尚有 {remaining_consensus_voters} 位待回覆。")]
                        )
                    )
                
                # 儲存使用者訊息並返回
                # 💡 使用 UTC 時區
                messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})
                return "OK"
            
            # ====== 階段五：處理最終投票回覆 (選擇最終候選人或活動) ======
            final_vote_match = FINAL_VOTE_PATTERN.match(msg_text)
            if (current_phase == 'main' or current_phase == 'warmup') and final_vote_match and exp_data.get("final_vote_sent") == True:
                final_choice = final_vote_match.group(1).strip() 
                
                final_votes_ref = exp_doc_ref.collection("final_votes")
                # 儲存最終投票
                final_votes_ref.document(user_id).set({
                    "user_id": user_id, 
                    "choice": final_choice, 
                    "timestamp": datetime.now(timezone.utc).isoformat() # 統一使用 UTC 時區
                })
                
                vote_docs = list(final_votes_ref.stream())
                
                if len(vote_docs) >= TEAM_SIZE:
                    
                    choices = [doc.to_dict().get("choice") for doc in vote_docs]
                    vote_counts = Counter(choices)
                    # 💡 檢查是否完全一致
                    is_unanimous = len(vote_counts) == 1 and next(iter(vote_counts.values())) >= TEAM_SIZE
                    
                    # 取得最終結果 (如果一致)
                    final_result = next(iter(vote_counts.keys())) if is_unanimous else None
                    
                    if is_unanimous:
                        
                        # 記錄結束時間 (UTC)
                        end_time = datetime.now(timezone.utc)
                        
                        # 確保 start_time_dt_from_db 是 datetime 且為 UTC-aware
                        start_time_dt_from_db = exp_data.get("start_time")
                        if isinstance(start_time_dt_from_db, datetime):
                            if start_time_dt_from_db.tzinfo is None or start_time_dt_from_db.tzinfo.utcoffset(start_time_dt_from_db) is None:
                                start_time_dt_from_db = start_time_dt_from_db.replace(tzinfo=timezone.utc)
                        else:
                            # 如果 start_time_dt_from_db 無效，則使用 end_time 來防止計算出負數或錯誤
                            start_time_dt_from_db = end_time 

                        # 計算總時長
                        total_experiment_duration = end_time - start_time_dt_from_db
                        duration_formatted = format_duration(total_experiment_duration)

                        # 💡 根據階段建構結束訊息 (優化後的邏輯)
                        result_type = "最終活動" if current_phase == 'warmup' else "最終候選人"
                        message = f"恭喜！團隊已達成共識，{result_type}選擇為{final_result}。\n本次決策圓滿結束，總討論時長：{duration_formatted}。"
                            
                        # 顧問風格回應：決策結束
                        line_bot_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextSendMessage(text=message)]
                            )
                        )
                        
                        # 實驗結束，更新實驗狀態，記錄結束時間及總時長
                        exp_doc_ref.update({
                            "status": "completed", 
                            "end_time": end_time, # 儲存 UTC datetime
                            "total_duration_seconds": total_experiment_duration.total_seconds(),
                            "total_duration_formatted": duration_formatted # 儲存 MM:SS 格式
                        })
                    else:
                        # 投票未一致，提醒繼續溝通
                        remaining_text = get_remaining_time_text(start_time_dt, current_phase)
                        # 顧問風格回應：提醒繼續溝通
                        line_bot_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextSendMessage(text=f"看起來團隊對於最終選擇尚未達成一致 。投票結果：{dict(vote_counts)})。\n\n討論時間剩{remaining_text}，請團隊利用時間再次溝通。")]
                            )
                        )
                else:
                    # 尚未達到投票人數
                    remaining_final_voters = TEAM_SIZE - len(vote_docs)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextSendMessage(text=f"目前 {len(vote_docs)}位成員已完成最終決策，尚有 {remaining_final_voters} 位待回覆。")]
                        )
                    )
                
                # 無論結果如何，都記錄這次的最終投票訊息
                # 💡 使用 UTC 時區
                messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})
                return "OK"
            
            # ====== 階段六：處理初始投票/一般訊息 ======
            
            vote_match = VOTE_PATTERN.match(msg_text)

            vote_recorded = False # 新增旗標：是否為投票訊息
            if vote_match:
                choice_text = vote_match.group(1).strip() 
                
                # 1. Store/Update the vote in the subcollection (document ID is user_id)
                votes_collection_ref.document(user_id).set({
                    "user_id": user_id, 
                    "choice": choice_text, 
                    "timestamp": datetime.now(timezone.utc).isoformat(), # 統一使用 UTC 時區
                    "source_text": msg_text 
                })
                
                # 2. 💡 重新從 Firestore 串流計算當前準確的投票人數
                vote_docs = list(votes_collection_ref.stream())
                current_votes_count = len(vote_docs) 
                
                # 3. 💡 更新主文件中的計數以保持記錄一致
                exp_doc_ref.update({"votes_count": current_votes_count})
                
                vote_recorded = True # 標記為投票訊息

            # 記錄訊息 (非特殊指令或階段的預設行為)
            # 💡 使用 UTC 時區
            messages_collection_ref.add({ 
                "user_id": user_id, 
                "text": msg_text, 
                "timestamp": datetime.now(timezone.utc).isoformat(), # 統一使用 UTC 時區
                "from": "user"
            })
            
            # 💡 [新增] 檢查是否剛好達到人數，且尚未發送討論提示
            if vote_recorded and current_votes_count == TEAM_SIZE and not discussion_prompt_sent:
                
                discussion_message = "👏 團隊所有成員已完成初始投票！現在，請大家開始討論各自支持的理由，共同推進決策。"
                
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextSendMessage(text=discussion_message)]
                    )
                )
                
                # 更新旗標，確保只發送一次
                exp_doc_ref.update({"discussion_prompt_sent": True})
                return "OK" # 投票完成並發送提示，流程結束

            # 檢查初始投票人數是否已達標 (>= TEAM_SIZE) - 僅在未達標時顯示提示
            if current_votes_count < TEAM_SIZE:
                remaining_people = TEAM_SIZE - current_votes_count 
                # flex_message = create_vote_flex_message(current_phase, current_role)
                # 顧問風格回應
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            TextSendMessage(text=f"目前有 {current_votes_count} 位成員已完成初始投票 (仍有 {remaining_people} 位待投票)。"),
                            # flex_message
                        ]
                    )
                )
                return "OK" 

            # 檢查訊息計數是否達到 6 (只有投票人數達標且未進入最終投票/時間未到期才執行 AI 邏輯)
            if exp_data.get("final_vote_sent") == False and duration.total_seconds() < total_duration.total_seconds():
                new_message_count = message_count + 1
                
                if new_message_count >= 3:
                    # 執行 AI 回覆邏輯 (請確保 'prompt.py' 和 'ask_assistant_with_role' 函數存在)
                    # 獲取最近的訊息歷史用於 AI 分析
                    recent_messages = []
                    messages_docs = list(messages_collection_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(20).stream())
                    
                    # 將訊息轉換為 AI 需要的格式
                    for msg_doc in reversed(messages_docs):  # 反轉以獲得正確的時間順序
                        msg_data = msg_doc.to_dict()
                        recent_messages.append({
                            "role": "user" if msg_data.get("from") == "user" else "assistant",
                            "content": msg_data.get("text", "")
                        })
                    
                    # 調用 AI 回覆
                    reply = ask_assistant_with_role(recent_messages, current_role, current_phase)
                    
                    # 💡 使用 UTC 時區
                    messages_collection_ref.add({
                        "user_id": current_role, 
                        "text": reply, 
                        "timestamp": datetime.now(timezone.utc).isoformat(), # 統一使用 UTC 時區
                        "from": "assistant"
                    })
                    
                    # AI 回應時，附上顧問角色口吻的計時器狀態和 AI 回應
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[
                                # TextSendMessage(text=get_remaining_time_text(start_time_dt, current_phase)),
                                TextSendMessage(text=reply)
                            ]
                        )
                    )

                    exp_doc_ref.update({"message_count": 0})
                else:
                    exp_doc_ref.update({"message_count": new_message_count})
            
            # 如果時間到期或已發送最終投票，僅記錄訊息，不進行額外回應。
            pass

    return "OK"

# ====== 啟動伺服器 ======
if __name__ == "__main__":
    port = int(os.getenv('PORT', 8080))
    print(f"🚀 應用程式啟動中，監聽埠號 {port}...")
    app.run(host='0.0.0.0', port=port) # 實際部署時使用
