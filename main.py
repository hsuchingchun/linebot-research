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
    create_final_selection_message
)

load_dotenv()
from prompt import ask_assistant_with_role

AI_ROLE_MAPPING = {
    "A組": "混合型AI",
    "B組": "整合型AI",
    "C組": "探究型 AI",
    "D組": "無介入AI",
}

# 暖身實驗：在第 1 分鐘檢查一次
WARMUP_CHECK_MINUTES = [1]
# 正式實驗：在第 2 分鐘和第 5 分鐘各檢查一次
MAIN_CHECK_MINUTES = [2, 5]

# 轉換分秒
WARMUP_CONSENSUS_CHECK_TIMES = [m * 60 for m in WARMUP_CHECK_MINUTES]
MAIN_CONSENSUS_CHECK_TIMES = [m * 60 for m in MAIN_CHECK_MINUTES]

# 實驗時長
WARMUP_DURATION_MINUTES = 2
MAIN_DURATION_MINUTES = 6
TEAM_SIZE = 2

# ====== Flask 和 Firebase 初始化 ======
app = Flask(__name__)

def get_firebase_credentials_from_env():
    """從環境變數讀取 Firebase 服務帳號金鑰。"""
    firebase_credentials = os.getenv("FIREBASE_CREDENTIALS")
    if not firebase_credentials:
        return None
    try:
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
            db = None
    except Exception as e:
        print(f"❌ Firebase 初始化失敗: {e}")
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
    if start_time_dt.tzinfo is None:
        start_time_dt = start_time_dt.replace(tzinfo=timezone.utc)
    elapsed_time = datetime.now(timezone.utc) - start_time_dt
    remaining_time = total_duration - elapsed_time
    remaining_text = format_duration(remaining_time)
    if remaining_time.total_seconds() <= 0:
         return "✅ 討論時間已結束"
    elif remaining_time.total_seconds() < 60:
         return f"⚠️ 討論進入倒數階段，時間剩餘 {remaining_text}"
    return f"我們時間還剩 {remaining_text}，仍有充裕的時間"

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

                # 💡【修正】在開始新實驗前，強制刪除所有可能殘留的舊投票紀錄
                # 1. 刪除舊的共識投票紀錄
                consensus_votes_ref_to_delete = exp_doc_ref.collection("consensus_votes")
                delete_collection(consensus_votes_ref_to_delete, 10)

                # 2. 刪除這個階段對應的初始/最終投票紀錄，確保實驗乾淨
                votes_collection_to_delete_name = get_votes_collection_name(phase)
                votes_collection_to_delete_ref = exp_doc_ref.collection(votes_collection_to_delete_name)
                delete_collection(votes_collection_to_delete_ref, 10)
                
                # 重置實驗狀態文件
                exp_doc_ref.set({
                    "group_id": source_id, "bot_role": bot_role, "phase": phase,
                    "start_time": now_utc, "status": "running",
                    "message_count": 0, "votes_count": 0,
                    "consensus_checks_sent_count": 0,
                    "final_vote_sent": False,
                    "discussion_prompt_sent": False
                })
                
                flex_message = create_vote_flex_message(phase, bot_role)
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
            
            vote_collection_name = get_votes_collection_name(current_phase)
            votes_collection_ref = exp_doc_ref.collection(vote_collection_name)
            all_vote_docs = list(votes_collection_ref.stream())
            current_initial_votes_count = sum(1 for doc in all_vote_docs if doc.to_dict().get("vote_type", "initial") == "initial")

            start_time_dt = exp_data.get("start_time")
            duration = timedelta(seconds=0)
            total_duration = timedelta(minutes=get_phase_duration_minutes(current_phase))
            
            if isinstance(start_time_dt, datetime):
                if start_time_dt.tzinfo is None:
                    start_time_dt = start_time_dt.replace(tzinfo=timezone.utc)
                duration = datetime.now(timezone.utc) - start_time_dt
            
            message_collection_name = get_messages_collection_name(current_phase)
            messages_collection_ref = exp_doc_ref.collection(message_collection_name)

            # ====== 階段二：多時間點共識檢查介入 ======
            if not exp_data.get("final_vote_sent"):
                check_times = WARMUP_CONSENSUS_CHECK_TIMES if current_phase == 'warmup' else MAIN_CONSENSUS_CHECK_TIMES
                checks_sent_count = exp_data.get("consensus_checks_sent_count", 0)
                
                if checks_sent_count < len(check_times):
                    next_check_time = check_times[checks_sent_count]
                    if duration.total_seconds() >= next_check_time and current_initial_votes_count >= TEAM_SIZE:
                        try:
                            line_bot_api.push_message(
                                PushMessageRequest(
                                    to=source_id,
                                    messages=[create_consensus_check_message(current_phase, format_duration(duration))]
                                )
                            )
                            exp_doc_ref.update({"consensus_checks_sent_count": checks_sent_count + 1})
                            print(f"✅ 已發送第 {checks_sent_count + 1} 次共識檢查。")
                        except Exception as e:
                            print(f"❌ 發送共識檢查 Push Message 失敗: {e}")

            # ====== 階段三：時間到期觸發最終投票 (此階段只發送訊息，不停止計時) ======
            if not exp_data.get("final_vote_sent") and duration >= total_duration:
                initial_vote_status_text = ""
                if current_initial_votes_count < TEAM_SIZE:
                    initial_vote_status_text = f"\n⚠️ 注意：初始投票人數不足 ({current_initial_votes_count}/{TEAM_SIZE})，但討論時間已結束。"
                
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            TextSendMessage(text=f"🚨 時間到！實驗的 {get_phase_duration_minutes(current_phase)} 分鐘討論時間已結束，請團隊進行最終決策。{initial_vote_status_text}"),
                            create_final_selection_message(current_phase)
                        ]
                    )
                )
                exp_doc_ref.update({"final_vote_sent": True})
                messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})
                return "OK"
            
            # ====== 階段四：處理共識檢查回覆 ======
            consensus_match = CONSENSUS_PATTERN.match(msg_text)
            if consensus_match and not exp_data.get("final_vote_sent"):
                choice = consensus_match.group(1).strip() 
                consensus_votes_ref = exp_doc_ref.collection("consensus_votes")
                consensus_votes_ref.document(user_id).set({
                    "user_id": user_id, "choice": choice, "timestamp": datetime.now(timezone.utc).isoformat()
                })
                vote_docs_consensus = list(consensus_votes_ref.stream())
                if len(vote_docs_consensus) >= TEAM_SIZE:
                    all_consensus = all(doc.to_dict().get("choice") == "已有共識" for doc in vote_docs_consensus)
                    if all_consensus:
                        line_bot_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[
                                    TextSendMessage(text="太棒了！團隊一致確認已達成共識，現在請大家進行最終選擇。"),
                                    create_final_selection_message(current_phase)
                                ]
                            )
                        )
                        exp_doc_ref.update({"final_vote_sent": True})
                    else:
                        line_bot_api.reply_message(
                            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text=f"目前看來部分成員建議繼續討論。 {get_remaining_time_text(start_time_dt, current_phase)}，請繼續交流意見。")])
                        )
                else:
                    remaining_voters = TEAM_SIZE - len(vote_docs_consensus)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text=f"目前有 {len(vote_docs_consensus)} 位成員已確認共識，尚有 {remaining_voters} 位待回覆。")])
                    )
                messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})
                return "OK"
            
            # ====== 階段五：處理最終投票回覆 ======
            final_vote_match = FINAL_VOTE_PATTERN.match(msg_text)
            if final_vote_match and exp_data.get("final_vote_sent"):
                final_choice = final_vote_match.group(1).strip()
                final_votes_ref = exp_doc_ref.collection(get_votes_collection_name(current_phase))

                final_votes_ref.document(user_id).set({
                    "final_choice": final_choice,
                    "timestamp_final": datetime.now(timezone.utc).isoformat(),
                    "vote_type": "final"
                }, merge=True)

                final_vote_docs = list(final_votes_ref.where("vote_type", "==", "final").stream())
                
                if len(final_vote_docs) >= TEAM_SIZE:
                    choices = [doc.to_dict().get("final_choice") for doc in final_vote_docs]
                    is_unanimous = len(set(choices)) == 1
                    
                    if is_unanimous:
                        end_time = datetime.now(timezone.utc)
                        total_experiment_duration = end_time - start_time_dt
                        duration_formatted = format_duration(total_experiment_duration)
                        final_result = choices[0]
                        
                        result_type = "最終活動" if current_phase == 'warmup' else "最終候選人"
                        message = f"恭喜！團隊已達成共識，{result_type}選擇為{final_result}。\n本次決策圓滿結束，總討論時長：{duration_formatted}。"
                        
                        line_bot_api.reply_message(
                            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text=message)])
                        )
                        
                        exp_doc_ref.update({
                            "status": "completed", 
                            "end_time": end_time,
                            "total_duration_seconds": total_experiment_duration.total_seconds(),
                            "total_duration_formatted": duration_formatted
                        })
                    else:
                        vote_counts = Counter(choices)
                        line_bot_api.reply_message(
                            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text=f"看起來團隊對於最終選擇尚未達成一致。投票結果：{dict(vote_counts)}。\n請團隊利用時間再次溝通。")])
                        )
                else:
                    remaining_voters = TEAM_SIZE - len(final_vote_docs)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text=f"目前 {len(final_vote_docs)} 位成員已完成最終決策，尚有 {remaining_voters} 位待回覆。")])
                    )
                
                messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})
                return "OK"
            
            # ====== 階段六：處理初始投票/一般訊息 ======
            vote_match = VOTE_PATTERN.match(msg_text)
            if vote_match:
                if not exp_data.get("final_vote_sent"):
                    choice_text = vote_match.group(1).strip()
                    votes_collection_ref.document(user_id).set({
                        "user_id": user_id, 
                        "initial_choice": choice_text, 
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        # "source_text": msg_text,
                        "vote_type": "initial"
                    })
                    
                    all_vote_docs_after_vote = list(votes_collection_ref.stream())
                    current_initial_votes_count_after_vote = sum(1 for doc in all_vote_docs_after_vote if doc.to_dict().get("vote_type", "initial") == "initial")

                    exp_doc_ref.update({"votes_count": current_initial_votes_count_after_vote})
                    
                    if current_initial_votes_count_after_vote == TEAM_SIZE and not exp_data.get("discussion_prompt_sent"):
                        line_bot_api.reply_message(
                            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text="👏 團隊所有成員已完成初始投票！現在，請大家開始討論各自支持的理由，共同推進決策。")])
                        )
                        exp_doc_ref.update({"discussion_prompt_sent": True})
                    elif current_initial_votes_count_after_vote < TEAM_SIZE:
                        remaining_people = TEAM_SIZE - current_initial_votes_count_after_vote
                        line_bot_api.reply_message(
                            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextSendMessage(text=f"目前有 {current_initial_votes_count_after_vote} 位成員已完成初始投票 (仍有 {remaining_people} 位待投票)。")])
                        )
                    return "OK"

            # 記錄所有一般訊息 (如果不是投票訊息)
            messages_collection_ref.add({"user_id": user_id, "text": msg_text, "timestamp": datetime.now(timezone.utc).isoformat(), "from": "user"})
            
            # AI 回覆邏輯 (只有在時間未到、未進入最終投票、且初始投票完成時才觸發)
            if not exp_data.get("final_vote_sent") and duration < total_duration and current_initial_votes_count >= TEAM_SIZE:
                message_count = exp_data.get("message_count", 0)
                new_message_count = message_count + 1
                if new_message_count >= 3:
                    recent_messages = []
                    messages_docs = list(messages_collection_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(20).stream())
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