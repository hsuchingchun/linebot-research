from flask import Flask, request
from linebot import LineBotApi, WebhookParser
from linebot.models import MessageEvent, TextMessage, TextSendMessage, SourceGroup, SourceRoom
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import os
import json
import re

from dotenv import load_dotenv
load_dotenv()

from prompt import ask_assistant_with_role

app = Flask(__name__)

# ====== Firebase 初始化 ======
def get_firebase_credentials_from_env():
    """從環境變數讀取 Firebase 服務帳號金鑰。"""
    firebase_credentials = os.getenv("FIREBASE_CREDENTIALS")
    if not firebase_credentials:
        raise ValueError("FIREBASE_CREDENTIALS 環境變數未設定。")
    try:
        service_account_info = json.loads(firebase_credentials)
        print("✅ 成功從環境變數讀取 Firebase 金鑰")
        return credentials.Certificate(service_account_info)
    except json.JSONDecodeError:
        raise ValueError("FIREBASE_CREDENTIALS 環境變數格式錯誤，請確保它是單行且用單引號包覆的 JSON 字串。")

if not firebase_admin._apps:
    firebase_cred = get_firebase_credentials_from_env()
    firebase_admin.initialize_app(firebase_cred)
db = firestore.client()

# ====== LINE Bot 初始化 ======
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
parser = WebhookParser(os.getenv("LINE_CHANNEL_SECRET"))

# 正規表示式，用來解析「開始新實驗」指令
COMMAND_PATTERN = re.compile(r"@機器人 開始新實驗 (.+)")

# ====== Webhook 路由入口 ======
@app.route("/callback", methods=["POST"])
def webhook():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    events = parser.parse(body, signature)

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessage):
            # 取得群組 ID
            if isinstance(event.source, SourceGroup):
                source_id = event.source.group_id
            elif isinstance(event.source, SourceRoom):
                source_id = event.source.room_id
            else:
                # 不在群組或聊天室，不處理
                return "OK"

            user_id = event.source.user_id
            msg_text = event.message.text.strip()

            # 檢查是否為「開始新實驗」指令
            match = COMMAND_PATTERN.match(msg_text)
            if match:
                bot_role = match.group(1).strip()
                # 建立新的實驗紀錄，並初始化訊息計數器
                exp_doc_ref = db.collection("experiments").document(source_id)
                exp_doc_ref.set({
                    "group_id": source_id,
                    "bot_role": bot_role,
                    "created_at": datetime.now(),
                    "message_count": 0  # 💡 新增訊息計數器
                })
                reply_text = f"✅ 已成功建立新的實驗，機器人角色設定為：「{bot_role}」。請開始你的對話。"
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
                return "OK"

            # 取得當前實驗的 bot_role 和訊息計數
            exp_doc_ref = db.collection("experiments").document(source_id)
            exp_doc = exp_doc_ref.get()
            if not exp_doc.exists:
                # 尚未開始實驗，引導使用者
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 請先輸入指令來開始一個新實驗，例如：`@機器人 開始新實驗 AI類型`"))
                return "OK"

            exp_data = exp_doc.to_dict()
            current_role = exp_data.get("bot_role")
            message_count = exp_data.get("message_count", 0) # 💡 讀取計數器

            # 處理使用者訊息並儲存
            messages_collection_ref = exp_doc_ref.collection("messages")
            timestamp = datetime.now().isoformat()
            messages_collection_ref.add({
                "user_id": user_id,
                "text": msg_text,
                "timestamp": timestamp,
                "from": "user"
            })
            
            # 💡 檢查訊息計數是否達到 6
            new_message_count = message_count + 1
            if new_message_count >= 6:
                # 讀取對話歷史（最近 20 筆）
                history_query = messages_collection_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(20)
                history_docs = list(history_query.stream())
                history_docs_reversed = list(reversed(history_docs))

                # 轉成 ChatCompletion messages 格式
                messages = []
                for doc in history_docs_reversed:
                    data = doc.to_dict()
                    role = "user" if data.get("from") == "user" else "assistant"
                    messages.append({"role": role, "content": data.get("text", "")})
                
                # 呼叫 AI 助手，並傳入角色
                try:
                    if not current_role:
                        current_role = "不介入AI" # 設定一個預設值

                    reply = ask_assistant_with_role(messages, current_role)

                    # 儲存 AI 回覆
                    messages_collection_ref.add({
                        "user_id": current_role,
                        "text": reply,
                        "timestamp": datetime.now().isoformat(),
                        "from": "assistant"
                    })

                    # 回覆 LINE
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text=reply)
                    )

                    # 💡 重設訊息計數
                    exp_doc_ref.update({"message_count": 0})
                
                except Exception as e:
                    print(f"❌ AI 回應失敗：{e}")
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text="⚠️ AI 回應失敗，請稍後再試。")
                    )
            else:
                # 💡 如果還沒有達到 3 則只更新計數器
                exp_doc_ref.update({"message_count": new_message_count})
                # 不回覆使用者
                pass

    return "OK"

# ====== 啟動伺服器 ======
if __name__ == "__main__":
    port = int(os.getenv('PORT', 8080))
    print(f"🚀 應用程式啟動中，監聽埠號 {port}...")
    app.run(host='0.0.0.0', port=port)
