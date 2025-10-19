import firebase_admin
from firebase_admin import credentials, firestore
import os
import json
import pandas as pd

from dotenv import load_dotenv
load_dotenv()

def get_firebase_credentials_from_env():
    """從環境變數讀取 Firebase 服務帳號金鑰。"""
    firebase_credentials = os.getenv("FIREBASE_CREDENTIALS")
    if not firebase_credentials:
        raise ValueError("FIREBASE_CREDENTIALS 環境變數未設定。")
    try:
        service_account_info = json.loads(firebase_credentials)
        return credentials.Certificate(service_account_info)
    except json.JSONDecodeError:
        raise ValueError("FIREBASE_CREDENTIALS 環境變數格式錯誤。")

# 初始化 Firebase
try:
    if not firebase_admin._apps:
        firebase_cred = get_firebase_credentials_from_env()
        firebase_admin.initialize_app(firebase_cred)
    db = firestore.client()
    print("✅ 成功連接 Firebase")
except Exception as e:
    print(f"❌ Firebase 連接失敗: {e}")
    exit()

def download_and_preprocess_data():
    """
    從 Firebase 下載所有實驗的對話資料，並轉換為一個 Pandas DataFrame。
    """
    all_messages = []
    
    experiments_ref = db.collection("experiments")
    experiments_docs = experiments_ref.stream()

    for exp_doc in experiments_docs:
        exp_data = exp_doc.to_dict()
        experiment_id = exp_doc.id
        bot_role = exp_data.get("bot_role", "未知")

        print(f"處理實驗 ID: {experiment_id}，機器人角色: {bot_role}")
        
        messages_ref = db.collection("experiments").document(experiment_id).collection("messages")
        messages_docs = messages_ref.order_by("timestamp").stream()

        for msg_doc in messages_docs:
            msg_data = msg_doc.to_dict()
            msg_data['experiment_id'] = experiment_id
            msg_data['bot_role'] = bot_role
            all_messages.append(msg_data)

    if not all_messages:
        print("⚠️ 未找到任何對話資料。")
        return pd.DataFrame()

    df = pd.DataFrame(all_messages)
    
    # 資料清理與型態轉換
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 建立一個標示誰發言的欄位
    # 如果 user_id 是 '不介入AI'、'資訊提供者AI' 等，則視為 AI 發言
    # 💡 根據您的程式碼，AI 發言的 user_id 會是它的角色名稱。
    ai_roles = ['整合型AI', '混合型AI', '探究型AI', '無介入AI'] # 請根據您的實際角色名稱調整
    df['speaker_type'] = df.apply(
        lambda row: 'ai' if row['user_id'] in ai_roles or row['from'] == 'assistant' else 'user',
        axis=1
    )
    
    print("✅ 資料下載並轉換完成！")
    return df

# 執行函式
df = download_and_preprocess_data()
if not df.empty:
    print("\nDataFrame 範例：")
    print(df.head())
    print("\n資料欄位：", df.columns)
    
    # 將 DataFrame 存成 CSV 檔，方便後續分析
    df.to_csv("conversation_data.csv", index=False, encoding='utf-8-sig')
    print("\n✅ 資料已儲存為 conversation_data.csv")



# # 計算各成員的共同資訊與私有資訊數量
# information_counts = df.groupby(['user_id', 'information_type']).size().unstack(fill_value=0)
# print("\n資訊揭露數量統計：")
# print(information_counts)
