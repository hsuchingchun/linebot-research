import os
from dotenv import load_dotenv
from flask import Flask, request
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, PushMessageRequest
)
from linebot.v3.messaging.models import (
    TextMessage as TextSendMessage,
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
import certifi

from flex_templates import (
    create_vote_flex_message,
    create_consensus_check_message,
    create_final_selection_message,
    create_position_message 
)

from google.cloud.firestore_v1.base_query import FieldFilter

load_dotenv()
from prompt import ask_assistant_with_role

AI_ROLE_MAPPING = {
    "A組": "混合型AI",
    "B組": "整合型AI",
    "C組": "探究型AI",
    "D組": "無介入AI",
}

# 暖身實驗：在第 6 分鐘檢查一次
WARMUP_CHECK_MINUTES = [1]
# 正式實驗：在第 15 分鐘和第 20 分鐘各檢查一次
MAIN_CHECK_MINUTES = [1,2]

# 轉換分秒
WARMUP_CONSENSUS_CHECK_TIMES = [m * 60 for m in WARMUP_CHECK_MINUTES]
MAIN_CONSENSUS_CHECK_TIMES = [m * 60 for m in MAIN_CHECK_MINUTES]

# 實驗時長
WARMUP_DURATION_MINUTES = 2
MAIN_DURATION_MINUTES = 3
TEAM_SIZE = 2 # 預設團隊大小，可根據實際情況調整
POSITIONS_ALLOWED = ["行銷長", "營運長", "人資長"] # 允許的職位清單
AI_REPLY_TURN = 4 # ai 會在幾則訊息後回覆

# ====== Flask 和 Firebase 初始化 ======
app = Flask(__name__)

# =====本地測試使用=====
# def get_firebase_credentials_from_env():
#     """從環境變數讀取 Firebase 服務帳號金鑰。"""
#     firebase_credentials = os.getenv("FIREBASE_CREDENTIALS")
#     if not firebase_credentials:
#         return None
#     try:
#         service_account_info = json.loads(firebase_credentials)
#         print("✅ 成功從環境變數讀取 Firebase 金鑰")
#         return credentials.Certificate(service_account_info)
#     except json.JSONDecodeError:
#         raise ValueError("FIREBASE_CREDENTIALS 環境變數格式錯誤，請確保它是單行且用單引號包覆的 JSON 字串。")

# if not firebase_admin._apps:
#     try:
#         firebase_cred = get_firebase_credentials_from_env()
#         if firebase_cred:
#             firebase_admin.initialize_app(firebase_cred)
#             db = firestore.client()
#         else:
#             print("⚠️ 未找到 Firebase 憑證，Firestore 功能將無法使用。")
#             db = None
#     except Exception as e:
#         print(f"❌ Firebase 初始化失敗: {e}")
#         db = None

# ====== GCP 適用 ======
if not firebase_admin._apps:
    try:
        # 💡 GCP 修正：在 GCP 環境中，Firestore 會自動使用服務帳號
        # 移除依賴環境變數 FIREBASE_CREDENTIALS 的本地文件讀取邏輯
        firebase_admin.initialize_app()
        db = firestore.client()
        print("✅ Firebase 已使用 Application Default Credentials 成功初始化")
    except Exception as e:
        print(f"❌ Firebase 初始化失敗: {e}")
        # 如果在 GCP 上運行，但初始化失敗，應該拋出錯誤
        db = None

# ====== 時間及階段輔助函數 ======
def get_phase_duration_minutes(phase: str) -> int:
    return WARMUP_DURATION_MINUTES if phase == 'warmup' else MAIN_DURATION_MINUTES

def format_duration(td: timedelta) -> str:
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        total_seconds = 0
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"

def get_remaining_time_text(start_time_dt: datetime, current_phase: str) -> str:
    total_duration = timedelta(minutes=get_phase_duration_minutes(current_phase))
    # 💡 修正：如果 start_time_dt 為 None (暖身階段開始計時前)，則回傳等待計時的文字
    if start_time_dt is None:
        return "等待團隊確立職位後開始計時"

    if start_time_dt.tzinfo is None:
        start_time_dt = start_time_dt.replace(tzinfo=timezone.utc)
    elapsed_time = datetime.now(timezone.utc) - start_time_dt
    remaining_time = total_duration - elapsed_time
    remaining_text = format_duration(remaining_time)
    if remaining_time.total_seconds() <= 0:
         return "討論時間已結束！"
    elif remaining_time.total_seconds() < 60:
         return f"討論進入倒數階段，時間剩餘 {remaining_text}。"
    return f"我們時間還剩 {remaining_text}，仍有充裕的時間。"
    
def get_consensus_collection_name(phase: str) -> str:
    return "warmup_consensus_votes" if phase == 'warmup' else "main_consensus_votes"


# 除錯列印：印出目前實驗執行時間（經過與剩餘）
def print_current_experiment_time(start_time_dt: datetime, current_phase: str) -> None:
    try:
        total_duration = timedelta(minutes=get_phase_duration_minutes(current_phase))
        if not isinstance(start_time_dt, datetime):
            print("⌛ 實驗時間：尚未開始計時")
            return
        if start_time_dt.tzinfo is None:
            start_time_dt = start_time_dt.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - start_time_dt
        remaining = total_duration - elapsed
        elapsed_text = format_duration(elapsed)
        remaining_text = format_duration(remaining)
        print(f"⌛ 實驗時間：已過 {elapsed_text}，剩餘 {remaining_text}（階段：{current_phase}）")
    except Exception:
        pass

def get_messages_collection_name(phase: str) -> str:
    return "warmup_messages" if phase == 'warmup' else "main_messages"

def get_votes_collection_name(phase: str) -> str:
    return "warmup_votes" if phase == 'warmup' else "main_votes"

def delete_collection(coll_ref, batch_size):
    """分批刪除一個集合中的所有文件，以避免超時。"""
    try:
        docs = coll_ref.limit(batch_size).stream()
        deleted = 0
        for doc in docs:
            doc.reference.delete()
            deleted += 1

        if deleted >= batch_size:
            return delete_collection(coll_ref, batch_size)
        print(f"✅ 成功刪除舊的集合資料")
    except Exception as e:
        print(f"⚠️ 刪除集合時發生錯誤")

# ====== LINE Bot 初始化與正規表達式 ======
channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
channel_secret = os.getenv("LINE_CHANNEL_SECRET")
configuration = Configuration(access_token=channel_access_token, ssl_ca_cert=certifi.where())
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)
parser = WebhookParser(channel_secret)

COMMAND_PATTERN = re.compile(r"@AI顧問 開始(暖身|正式)實驗 (.+)")
POSITION_PATTERN = re.compile(r"^我的職位是(行銷長|營運長|人資長)$") # 支援『行銷長/營運長/人資長』
VOTE_PATTERN = re.compile(r"^我選(玩桌遊|公益淨灘|包場看電影| Amy| Sally| Nancy)$")
CONSENSUS_PATTERN = re.compile(r"^(已有共識|需要再討論)$")
FINAL_VOTE_PATTERN = re.compile(r"^我們最終選擇 (【玩桌遊】|【公益淨灘】|【包場看電影】|Amy|Sally|Nancy)$")

# ====== Webhook 路由入口 ======
@app.route("/callback", methods=["POST"])
def webhook():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    events = parser.parse(body, signature)

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            source = event.source
            source_id = getattr(source, 'group_id', None) or getattr(source, 'room_id', None)
            if not source_id:
                return "OK"

            user_id = getattr(source, 'user_id', None)
            msg_text = event.message.text.strip()
            
            if db is None:
                line_bot_api.reply_message(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text="❌ 系統服務異常：Firebase 資料庫未成功連線。")])
                )
                return "OK"

            # ====== 階段一：開始實驗指令 ======
            match = COMMAND_PATTERN.match(msg_text)
            if match:
                experiment_type = match.group(1).strip()
                input_role_code = match.group(2).strip()
                phase = 'warmup' if experiment_type == '暖身' else 'main'
                bot_role = AI_ROLE_MAPPING.get(input_role_code, input_role_code)
                exp_doc_ref = db.collection("experiments").document(source_id)
                now_utc = datetime.now(timezone.utc)

                # 💡 確保刪除所有共識相關記錄
                consensus_votes_warmup_ref_to_delete = exp_doc_ref.collection(get_consensus_collection_name('warmup'))
                delete_collection(consensus_votes_warmup_ref_to_delete, 10)
                # delete_collection(exp_doc_ref.collection(f"{get_consensus_collection_name('warmup')}_history"), 10)

                consensus_votes_main_ref_to_delete = exp_doc_ref.collection(get_consensus_collection_name('main'))
                delete_collection(consensus_votes_main_ref_to_delete, 10)
                # delete_collection(exp_doc_ref.collection(f"{get_consensus_collection_name('main')}_history"), 10)

                
                # 2. 刪除這個階段對應的初始/最終投票紀錄 (warmup_votes 或 main_votes)
                votes_collection_to_delete_name = get_votes_collection_name(phase)
                votes_collection_to_delete_ref = exp_doc_ref.collection(votes_collection_to_delete_name)
                delete_collection(votes_collection_to_delete_ref, 10)
                
                # 重置實驗狀態文件
                exp_data_to_set = {
                    "group_id": source_id, "bot_role": bot_role, "phase": phase,
                    "status": "running", "message_count": 0, "votes_count": 0,
                    "consensus_checks_sent_count": 0, "final_vote_sent": False,
                    "discussion_prompt_sent": False,"final_decision_count": 0,
                    "consensus_reached_count":0,"consensus_failed_count":0
                }
                
                # 記錄活動開始時間
                if phase == 'warmup':
                    exp_data_to_set["warmup_start_time"] = now_utc
                else:  # main
                    exp_data_to_set["main_start_time"] = now_utc
                
                # 暖身階段需要先確立職位，正式階段直接開始
                if phase == 'warmup':
                    exp_data_to_set["positions_all_set"] = False
                    exp_data_to_set["start_time"] = None # 暖身階段先不計時
                    exp_data_to_set["team_size"] = TEAM_SIZE  
                    message_to_send = create_position_message()
                else:
                    exp_data_to_set["positions_all_set"] = True
                    exp_data_to_set["start_time"] = now_utc # 正式階段直接計時
                    exp_data_to_set["team_size"] = TEAM_SIZE
                    
                    # 💡 修正職位複製邏輯：從 warmup_votes 複製 position 紀錄到 main_votes
                    warmup_votes_ref = exp_doc_ref.collection(get_votes_collection_name('warmup'))
                    main_votes_ref = exp_doc_ref.collection(get_votes_collection_name('main'))
                    
                    # 獲取暖身階段所有包含 position 資訊的文件
                    warmup_docs_to_copy = list(warmup_votes_ref
                                               .where(filter=FieldFilter("vote_type", "in", ["position", "initial"]))
                                               .stream())
                    
                    # 將職位資訊複製到 main_votes 集合中
                    for doc in warmup_docs_to_copy:
                        data = doc.to_dict()
                        user_id_key = doc.id 
                        
                        # 💡 關鍵修正：只挑選和複製 position 欄位
                        copy_data = {
                            "user_id": user_id_key,
                            "timestamp": data.get("timestamp"),
                            "vote_type": "position", # 標記為職位紀錄 (作為 main_votes 的基礎文件)
                            "position": data.get("position", "未知職位"), # 確保複製 position
                        }
                        
                        # 職位資訊儲存到 main_votes 中
                        # 這裡使用 set 即可，因為 main_votes 集合剛被清空
                        main_votes_ref.document(user_id_key).set(copy_data)
                    
                    message_to_send = create_vote_flex_message(phase, bot_role)

                exp_doc_ref.set(exp_data_to_set)

                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            TextSendMessage(text=f"【{experiment_type}實驗】開始\n我是你們的 AI 顧問，在過程中協助大家一起進行決策✨。"),
                            message_to_send
                        ]
                    )
                )
                print(f"啟動【{phase}實驗】 組別: {input_role_code} 角色: {bot_role}")
                return "OK"

            # 取得當前實驗狀態
            exp_doc_ref = db.collection("experiments").document(source_id)
            exp_doc = exp_doc_ref.get()
            if not exp_doc.exists:
                line_bot_api.reply_message(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text="請您先輸入指令來啟動新的實驗流程 (例如: @AI顧問 開始暖身/正式實驗 組別)。")])
                )
                return "OK"

            exp_data = exp_doc.to_dict()

            if exp_data.get("status") == "completed":
                print("實驗已結束，忽略訊息。")
                return "OK"

            current_phase = exp_data.get("phase", "main")
            current_role = exp_data.get("bot_role")
            
            # 💡 判斷是否已開始計時 (即是否已設定 start_time)
            start_time_dt = exp_data.get("start_time")
            
            # 取得當前階段的 votes 集合參考
            vote_collection_name = get_votes_collection_name(current_phase)
            votes_collection_ref = exp_doc_ref.collection(vote_collection_name)

            if isinstance(start_time_dt, datetime):
                if start_time_dt.tzinfo is None:
                    start_time_dt = start_time_dt.replace(tzinfo=timezone.utc)
                duration = datetime.now(timezone.utc) - start_time_dt
                total_duration = timedelta(minutes=get_phase_duration_minutes(current_phase))
            else:
                # 暖身階段尚未確立職位，尚未計時
                duration = timedelta(seconds=0)
                total_duration = timedelta(seconds=0)
            
            # 💡 只有在開始計時後，才進行時間相關的檢查
            is_time_running = isinstance(start_time_dt, datetime)
            
            # 獲取投票計數 (用於共識檢查)
            all_vote_docs = list(votes_collection_ref.stream())
            current_initial_votes_count = sum(1 for doc in all_vote_docs if doc.to_dict().get("vote_type", "initial") == "initial")

            message_collection_name = get_messages_collection_name(current_phase)
            messages_collection_ref = exp_doc_ref.collection(message_collection_name)

            # ====== 輔助函數：獲取用戶職位 ======
            def get_user_position(user_id, votes_ref):
                """從 votes 集合中獲取用戶的職位資訊。"""
                # 職位資訊是以 user_id 為 document ID 儲存
                user_position_doc = votes_ref.document(user_id).get()
                if user_position_doc.exists:
                    # 檢查 document 中是否有 position 欄位
                    return user_position_doc.to_dict().get("position", "未知職位")
                return "未知職位"


            # ====== 階段一 A：處理職位選擇 (僅限暖身階段，且尚未確立職位) ======
            position_match = POSITION_PATTERN.match(msg_text)
            
            if current_phase == 'warmup' and not exp_data.get("positions_all_set") and position_match:
                position_choice = position_match.group(1).strip()
                
                # 💡 獲取實際團隊大小
                actual_team_size = exp_data.get("team_size", TEAM_SIZE)

                # 從投票記錄中檢查已選擇的職位
                all_position_docs = list(votes_collection_ref.where(filter=FieldFilter("vote_type", "==", "position")).stream())
                
                # 檢查職位是否已被選
                chosen_positions = [doc.to_dict().get("position") for doc in all_position_docs]
                
                if position_choice in chosen_positions:
                    print_current_experiment_time(start_time_dt, current_phase)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text=f"⚠️ {position_choice} 職位已被其他成員選走，請選擇其他職位。")])
                    )
                    messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})
                    return "OK"
                
                # 記錄用戶選擇的職位到 votes 集合
                votes_collection_ref.document(user_id).set({
                    "user_id": user_id, 
                    "position": position_choice, 
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "vote_type": "position" # 標記為職位選擇
                })

                # 重新獲取投票記錄，計算已選擇職位的人數
                all_position_docs_after = list(votes_collection_ref.where(filter=FieldFilter("vote_type", "==", "position")).stream())
                current_positions_count = len(all_position_docs_after)
                
                # 判斷是否所有成員都已選擇職位
                if current_positions_count >= actual_team_size:
                    # 所有人已選擇職位：觸發初始投票和計時
                    now_utc = datetime.now(timezone.utc)
                    
                    # 1. 更新狀態：設為已確立職位，並開始計時
                    exp_doc_ref.update({
                        "positions_all_set": True,
                        "start_time": now_utc # 這裡開始計時！
                    })
                    
                    flex_message = create_vote_flex_message(current_phase, current_role)
                    
                    # 2. 回覆訊息：先提示實驗開始計時，再發送初始投票 Flex Message
                    print_current_experiment_time(now_utc, current_phase) # 使用新的 start_time_dt 呼叫 print_current_experiment_time
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[
                                TextSendMessage(text="🎉 團隊所有成員已確立職位，暖身實驗計時開始！"),
                                TextSendMessage(text="現在請大家針對討論議題進行初始投票。"),
                                flex_message 
                            ]
                        )
                    )
                else:
                    # 尚未所有人選擇：回覆「還有幾個人未回覆」
                    remaining = actual_team_size - current_positions_count
                    print_current_experiment_time(start_time_dt, current_phase)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text=f"您已選擇{position_choice}。目前 {current_positions_count} 位成員已確立職位，尚有 {remaining} 位待選擇。")])
                    )
                
                messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})
                return "OK"


            # 💡 只有在計時開始後，才執行所有時間相關的階段 (階段二、三)
            if is_time_running:
                # ====== 階段二：多時間點共識檢查介入 ======
                if not exp_data.get("final_vote_sent"):
                    check_times = WARMUP_CONSENSUS_CHECK_TIMES if current_phase == 'warmup' else MAIN_CONSENSUS_CHECK_TIMES
                    checks_sent_count = exp_data.get("consensus_checks_sent_count", 0)
                    
                    if checks_sent_count < len(check_times):
                        next_check_time = check_times[checks_sent_count]
                        # 獲取實際團隊大小
                        actual_team_size = exp_data.get("team_size", TEAM_SIZE)
                        if duration.total_seconds() >= next_check_time and current_initial_votes_count >= actual_team_size:
                            try:
                                print_current_experiment_time(start_time_dt, current_phase)
                                line_bot_api.reply_message(
                                    ReplyMessageRequest(
                                        reply_token=event.reply_token,
                                        messages=[create_consensus_check_message(current_phase, format_duration(duration))]
                                    )
                                )
                                exp_doc_ref.update({"consensus_checks_sent_count": checks_sent_count + 1})
                                print(f"✅ 已發送第 {checks_sent_count + 1} 次共識檢查。")
                                return "OK"
                            except Exception as e:
                                print(f"❌ 發送共識檢查 Push Message 失敗: {e}")

                # ====== 階段三：時間到期觸發最終投票 (此階段只發送訊息，不停止計時) ======
                if not exp_data.get("final_vote_sent") and duration >= total_duration:
                    initial_vote_status_text = ""
                    # 獲取實際團隊大小
                    actual_team_size = exp_data.get("team_size", TEAM_SIZE)
                    if current_initial_votes_count < actual_team_size:
                        initial_vote_status_text = f"\n⚠️ 注意：初始投票人數不足 ({current_initial_votes_count}/{actual_team_size})，但討論時間已結束。"
                    
                    print_current_experiment_time(start_time_dt, current_phase)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[
                                TextSendMessage(text=f"🚨 時間到！實驗的 {get_phase_duration_minutes(current_phase)} 分鐘討論時間已結束，請團隊進行最終決策。{initial_vote_status_text}"),
                                create_final_selection_message(current_phase)
                            ]
                        )
                    )
                    # 🚨 關鍵修正：確保 exp_data 在內存中被更新，以處理緊隨其後的最終投票
                    exp_doc_ref.update({"final_vote_sent": True})
                    exp_data["final_vote_sent"] = True 
                    
                    messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})
                    # 注意：這裡保留 return "OK" 以強制流程隔離，依賴用戶發送新訊息。
                    return "OK"

            
            # ====== 階段四：處理共識檢查回覆 ======
            consensus_match = CONSENSUS_PATTERN.match(msg_text)
            if consensus_match and not exp_data.get("final_vote_sent") and is_time_running:
                choice = consensus_match.group(1).strip() 
                
                # 💡 修正 1: 根據階段獲取共識投票集合的參考 (這是用於當次回合計數的臨時集合)
                consensus_collection_name = get_consensus_collection_name(current_phase)
                consensus_votes_ref = exp_doc_ref.collection(consensus_collection_name)
                
                
                # 獲取實際團隊大小
                actual_team_size = exp_data.get("team_size", TEAM_SIZE)
                
                # 獲取用戶職位，方便記錄時包含更多資訊
                user_position = get_user_position(user_id, votes_collection_ref) 
                
                # 記錄當前回合的投票，用 user_id 作為文件 ID (確保每人只能投一次)
                current_timestamp = datetime.now(timezone.utc).isoformat()
                consensus_votes_ref.document(user_id).set({
                    "user_id": user_id, 
                    "choice": choice, 
                    "timestamp": current_timestamp,
                    "phase": current_phase, # 記錄當前階段
                    "position": user_position # 記錄職位
                })
                
                # 獲取當前所有共識投票紀錄
                vote_docs_consensus = list(consensus_votes_ref.stream())
                
                if len(vote_docs_consensus) >= actual_team_size:
                    # 💡 達到團隊人數，處理結果並清除計數
                    
                    all_consensus = all(doc.to_dict().get("choice") == "已有共識" for doc in vote_docs_consensus)
                    
                    if all_consensus:
                        # 情況一：達成共識
                        print_current_experiment_time(start_time_dt, current_phase)
                        line_bot_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[
                                    TextSendMessage(text="太棒了！團隊一致確認已達成共識，現在請大家進行最終選擇。"),
                                    create_final_selection_message(current_phase)
                                ]
                            )
                        )
                        # 🚨 關鍵修正：確保 exp_data 在內存中被更新、計算共識達成次數
                        exp_doc_ref.update({"final_vote_sent": True,"consensus_reached_count": firestore.Increment(1)})
                        exp_data["final_vote_sent"] = True 
                    else:
                        # 情況二：未達成共識
                        print_current_experiment_time(start_time_dt, current_phase)
                        # 💡 確保這裡回覆的訊息是「已重新開始計數」

                        progress_text = (
                            f"目前尚未達成一致共識，看來部分成員建議繼續討論。{get_remaining_time_text(start_time_dt, current_phase)}"
                            f"請團隊利用時間再次溝通，若有共識，成員可輸入「已有共識」來進入最終決策。"
                        )
                        line_bot_api.reply_message(
                            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text=progress_text)])
                        )

                        delete_collection(exp_doc_ref.collection(get_consensus_collection_name(current_phase)),10)

                        # 共識未達成次數
                        exp_doc_ref.update({"consensus_failed_count": firestore.Increment(1)})
                        exp_doc_ref.update({"final_vote_sent": False})
                    
                else:
                    # 情況三：尚未達到團隊人數
                    remaining_voters = actual_team_size - len(vote_docs_consensus)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text=f"目前有 {len(vote_docs_consensus)} 位成員已確認共識，尚有 {remaining_voters} 位待回覆。")])
                    )
                    
                messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})
                return "OK" # 階段四結束

            
            # ====== 階段五：處理最終投票回覆 ======
            final_vote_match = FINAL_VOTE_PATTERN.match(msg_text)
            if final_vote_match and exp_data.get("final_vote_sent"):
                final_choice = final_vote_match.group(1).strip()
                final_votes_ref = exp_doc_ref.collection(get_votes_collection_name(current_phase))

                # 從投票記錄中獲取該用戶的完整文件，以便保留 initial_choice 和 position
                user_vote_doc = final_votes_ref.document(user_id).get()
                current_vote_data = user_vote_doc.to_dict() if user_vote_doc.exists else {}
                
                # 記錄最終投票資訊，使用 merge=True
                update_data = {
                    "final_choice": final_choice,
                    "timestamp_final": datetime.now(timezone.utc).isoformat(),
                    "vote_type": "final", # 更新為 final
                    "user_id": user_id
                }
                # 保留 position 和 initial_choice 欄位（如果存在）
                if "position" in current_vote_data:
                    update_data["position"] = current_vote_data["position"]
                if "initial_choice" in current_vote_data:
                    update_data["initial_choice"] = current_vote_data["initial_choice"]

                final_votes_ref.document(user_id).set(update_data, merge=True)

                final_vote_docs = list(final_votes_ref.where(filter=FieldFilter("vote_type", "==", "final")).stream())
                
                # 獲取實際團隊大小
                actual_team_size = exp_data.get("team_size", TEAM_SIZE)
                if len(final_vote_docs) >= actual_team_size:
                    choices = [doc.to_dict().get("final_choice") for doc in final_vote_docs]
                    is_unanimous = len(set(choices)) == 1

                    # 在這裡增加最終決策次數 (只有當人數足夠時才算一次決策嘗試)
                    exp_doc_ref.update({"final_decision_count": firestore.Increment(1)})
                    
                    if is_unanimous:
                        end_time = datetime.now(timezone.utc)
                        total_experiment_duration = end_time - start_time_dt
                        duration_formatted = format_duration(total_experiment_duration)
                        final_result = choices[0]
                        
                        result_type = "最終活動" if current_phase == 'warmup' else "最終候選人"
                        message = f"恭喜！團隊已達成共識，{result_type}選擇為 {final_result}。\n本次決策圓滿結束，總討論時長：{duration_formatted}。"
                        
                        line_bot_api.reply_message(
                            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text=message)])
                        )
                        
                        # 記錄活動結束時間
                        update_data = {
                            "status": "completed", 
                            "end_time": end_time,
                            "total_duration_seconds": total_experiment_duration.total_seconds(),
                            "total_duration_formatted": duration_formatted
                        }
                        
                        if current_phase == 'warmup':
                            update_data["warmup_end_time"] = end_time
                        else:  # main
                            update_data["main_end_time"] = end_time
                            
                        exp_doc_ref.update(update_data)
                    else:
                        vote_counts = Counter(choices)
                        print_current_experiment_time(start_time_dt, current_phase)
                        # 1. 取得共識檢查次數列表，用於決定重置值
                            # 使用 Firestore.DELETE_FIELD
                        batch = db.batch()
                            
                        final_vote_docs_to_clear = final_votes_ref.where(filter=FieldFilter("vote_type", "==", "final")).stream()
                        for doc in final_vote_docs_to_clear:
                                doc_ref = final_votes_ref.document(doc.id)
                                batch.update(doc_ref, {
                                    "final_choice": firestore.DELETE_FIELD,
                                    "vote_type": firestore.DELETE_FIELD,
                                    "timestamp_final": firestore.DELETE_FIELD
                                })
                        batch.commit()
                        
                        # 檢查是否還有討論時間
                        vote_result_text = "、".join([f"{choice}: {count} 票" for choice, count in vote_counts.items()])
                        
                        if duration < total_duration:
                             # 情況 A: 還有時間，重置計數為 0，讓共識檢查繼續
                             # 還有時間，重置最終投票狀態，讓團隊繼續討論
                            exp_doc_ref.update({
                                "final_vote_sent": False,
                                "consensus_checks_sent_count": 0  # 重置共識檢查次數
                            })
                            line_bot_api.reply_message(
                                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text=f"看起來團隊對於最終選擇尚未達成一致。投票結果：{vote_result_text}。\n{get_remaining_time_text(start_time_dt, current_phase)}請團隊繼續溝通並重新達成共識。")])
                            )
                        else:
                           # 情況 B: 時間已到 (超時最終輪)
                            # 3. 回覆訊息：告知時間已到，但給予重新投票的機會，並發送 Flex Message
                            exp_doc_ref.update({
                                "final_vote_sent": True,
                                "consensus_checks_sent_count": TEAM_SIZE # ✅ 超時，設為最大值，禁用自動提醒
                            })

                            line_bot_api.reply_message(
                                ReplyMessageRequest(
                                    reply_token=event.reply_token, 
                                    messages=[
                                        TextSendMessage(text=f"時間已到，但團隊對於最終選擇尚未達成一致。投票結果：{vote_result_text}。"),
                                        TextSendMessage(text="請團隊立即進行最終輪投票，共同選出最終候選人。"), # 新增文字提示
                                        create_final_selection_message(current_phase) # 發送 Flex Message
                                    ]
                                )
                            )
                        return "OK"
                else:
                    remaining_voters = actual_team_size - len(final_vote_docs)
                    print_current_experiment_time(start_time_dt, current_phase)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text=f"目前 {len(final_vote_docs)} 位成員已完成最終決策，尚有 {remaining_voters} 位待回覆。")])
                    )
                
                messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})
                return "OK"
            
            # ====== 階段六：處理初始投票/一般訊息 ======
            vote_match = VOTE_PATTERN.match(msg_text)
            
            # 💡 關鍵變動：只有在 positions_all_set = True 且未到最終投票階段時，才允許初始投票
            if vote_match and exp_data.get("positions_all_set") and not exp_data.get("final_vote_sent"):
                
                # 從投票記錄中獲取用戶職位 (以便保留)
                user_position = get_user_position(user_id, votes_collection_ref)

                if not exp_data.get("final_vote_sent"):
                    choice_text = vote_match.group(1).strip()
                    
                    # 💡 關鍵修復：使用 merge=True，並確保傳入 position，這樣可以將 'position' 狀態
                    # 覆蓋為 'initial' 狀態，但保留 position 欄位和 user_id
                    votes_collection_ref.document(user_id).set({
                        "user_id": user_id, 
                        "initial_choice": choice_text, 
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "vote_type": "initial", # 這裡覆蓋為 initial
                        "position": user_position # 記錄職位 (從原文件讀取後再寫入，確保不丟失)
                    }, merge=True) 
                    
                    all_vote_docs_after_vote = list(votes_collection_ref.stream())
                    current_initial_votes_count_after_vote = sum(1 for doc in all_vote_docs_after_vote if doc.to_dict().get("vote_type", "initial") == "initial")

                    exp_doc_ref.update({"votes_count": current_initial_votes_count_after_vote})
                    
                    # 獲取實際團隊大小
                    actual_team_size = exp_data.get("team_size", TEAM_SIZE)
                    messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})

                    if current_initial_votes_count_after_vote == actual_team_size and not exp_data.get("discussion_prompt_sent"):
                        print_current_experiment_time(start_time_dt, current_phase)
                        line_bot_api.reply_message(
                            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text="👏 團隊所有成員已完成初始投票！現在，請大家開始討論各自支持的理由，共同推進決策。")])
                        )
                        exp_doc_ref.update({"discussion_prompt_sent": True})
                    elif current_initial_votes_count_after_vote < actual_team_size:
                        remaining_people = actual_team_size - current_initial_votes_count_after_vote
                        print_current_experiment_time(start_time_dt, current_phase)
                        line_bot_api.reply_message(
                            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text=f"目前有 {current_initial_votes_count_after_vote} 位成員已完成初始投票 (仍有 {remaining_people} 位待投票)。")])
                        )
                    return "OK"
            
            # 記錄所有一般訊息 (如果不是投票訊息)
            messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})
            
            # ====== 檢查是否有人自行輸入「已有共識」======
            # 💡 只有在計時開始後，才允許觸發共識檢查
            if msg_text.strip() == "已有共識" and not exp_data.get("final_vote_sent") and is_time_running:
                print_current_experiment_time(start_time_dt, current_phase)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[create_consensus_check_message(current_phase, format_duration(duration))]
                    )
                )
                return "OK"
            
            # AI 回覆邏輯：
            actual_team_size = exp_data.get("team_size", TEAM_SIZE)
            if is_time_running and not exp_data.get("final_vote_sent") and duration < total_duration and current_initial_votes_count >= actual_team_size:
                message_count = exp_data.get("message_count", 0)
                new_message_count = message_count + 1
                if new_message_count >= AI_REPLY_TURN:
                    recent_messages = []
                    messages_docs = list(messages_collection_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(35).stream())
                    for msg_doc in reversed(messages_docs):
                        msg_data = msg_doc.to_dict()
                        recent_messages.append({
                            "role": "user" if msg_data.get("from") == "user" else "assistant",
                            "content": msg_data.get("text", "")
                        })
                    reply = ask_assistant_with_role(recent_messages, current_role, current_phase)
                    messages_collection_ref.add({
                        "user_id": "AI_ASSISTANT", 
                        "text": reply, 
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "from": "assistant"
                    })
                    print_current_experiment_time(start_time_dt, current_phase)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextSendMessage(text=reply)]
                        )
                    )
                    exp_doc_ref.update({"message_count": 0})
                else:
                    exp_doc_ref.update({"message_count": new_message_count})
            
    return "OK"

if __name__ == "__main__":
    port = int(os.getenv('PORT', 8080))
    print(f"🚀 應用程式啟動中，監聽埠號 {port}...")
    app.run(host='0.0.0.0', port=port)