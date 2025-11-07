import firebase_admin
from firebase_admin import credentials, firestore
import json
from datetime import datetime
import os
from dotenv import load_dotenv
import pandas as pd # 匯入 pandas 函式庫

# 載入 .env 檔案中的環境變數
load_dotenv()

def get_firebase_credentials_from_env():
    """從環境變數讀取 Firebase 服務帳號金鑰的 JSON 字串。"""
    firebase_credentials_str = os.getenv("FIREBASE_CREDENTIALS")
    if not firebase_credentials_str:
        return None
    try:
        service_account_info = json.loads(firebase_credentials_str)
        return credentials.Certificate(service_account_info)
    except json.JSONDecodeError as e:
        print(f"❌ .env 中的 FIREBASE_CREDENTIALS 格式錯誤: {e}")
        return None
    except Exception as e:
        print(f"❌ 憑證處理時發生未知錯誤: {e}")
        return None

# --- Firebase 初始化 ---
try:
    if not firebase_admin._apps:
        firebase_cred = get_firebase_credentials_from_env()
        if firebase_cred:
            print("🔍 偵測到 FIREBASE_CREDENTIALS，正嘗試使用本地憑證...")
            firebase_admin.initialize_app(firebase_cred)
            print("✅ 成功使用 .env 中的憑證初始化 Firebase！")
        else:
            print("🔍 未偵測到本地憑證，正嘗試使用應用程式預設憑證 (ADC)...")
            firebase_admin.initialize_app()
            print("✅ 成功使用應用程式預設憑證初始化 Firebase！")
    
    db = firestore.client()
    print("🔗 Firebase 連線成功！")

except Exception as e:
    print(f"❌ Firebase 初始化失敗: {e}")
    exit()


def export_all_experiments_data(db_client):
    """
    從 Firestore 匯出所有實驗組的完整數據。
    """
    all_experiments_data = []
    
    experiments_ref = db_client.collection('experiments')
    docs = experiments_ref.stream()
    
    for doc in docs:
        group_id = doc.id
        experiment_data = doc.to_dict()
        
        # 處理時間戳記，轉換為字串以便 JSON 序列化
        for key, value in experiment_data.items():
            if isinstance(value, datetime):
                experiment_data[key] = value.isoformat()
        
        print(f"正在處理組別: {group_id} (AI 角色: {experiment_data.get('bot_role')})...")
        
        # 獲取 main_votes 子集合
        votes_ref = experiments_ref.document(group_id).collection('main_votes')
        votes_docs = votes_ref.stream()
        votes_list = []
        for vote_doc in votes_docs:
            vote_data = vote_doc.to_dict()
            for key, value in vote_data.items():
                if isinstance(value, datetime):
                    vote_data[key] = value.isoformat()
            votes_list.append(vote_data)
        experiment_data['main_votes_data'] = votes_list

        # 獲取 main_messages 子集合
        messages_ref = experiments_ref.document(group_id).collection('main_messages')
        messages_docs = messages_ref.stream()
        messages_list = []
        for msg_doc in messages_docs:
            msg_data = msg_doc.to_dict()
            for key, value in msg_data.items():
                if isinstance(value, datetime):
                    msg_data[key] = value.isoformat()
            messages_list.append(msg_data)
        experiment_data['main_messages_data'] = messages_list
        
        all_experiments_data.append(experiment_data)
        
    return all_experiments_data

def flatten_data_for_csv(all_data):
    """將巢狀的 JSON 數據扁平化，以生成三個獨立的 DataFrame。"""
    experiments_flat = []
    all_votes = []
    all_messages = []

    for exp_data in all_data:
        group_id = exp_data.get('group_id')
        bot_role = exp_data.get('bot_role')
        group_name = exp_data.get('group_name')

        # ✨ 步驟一：建立一個 "user_id" 到 "position" 的查詢字典
        # 我們從 votes 數據中提取這個對應關係
        user_position_map = {}
        for vote_record in exp_data.get('main_votes_data', []):
            user_id = vote_record.get('user_id')
            position = vote_record.get('position')
            # 只需要儲存一次
            if user_id and position and user_id not in user_position_map:
                user_position_map[user_id] = position
        
        # ✨ (可選) 為 AI 也加上 "職位"
        user_position_map['AI_ASSISTANT'] = 'AI_ASSISTANT'
        
        # 處理 votes
        for vote_record in exp_data.get('main_votes_data', []):
            vote_record['group_id'] = group_id # 加入關聯 ID
            vote_record['bot_role'] = bot_role
            vote_record['group_name'] = group_name
            all_votes.append(vote_record)
        
        # 處理 messages
        for message_record in exp_data.get('main_messages_data', []):
            message_record['group_id'] = group_id # 加入關聯 ID
            message_record['bot_role'] = bot_role
            message_record['group_name'] = group_name

            user_id = message_record.get('user_id')
            position = user_position_map.get(user_id, 'Unknown_User') # 使用 .get() 避免錯誤
            message_record['position'] = position

            all_messages.append(message_record)
            
        # 移除巢狀資料，準備主實驗表的數據
        exp_data.pop('main_votes_data', None)
        exp_data.pop('main_messages_data', None)
        experiments_flat.append(exp_data)

    # 因為您的 timestamp 是 ISO 格式的字串，直接按字母順序比較就能達到時間排序的效果。
    print("🔄 正在對所有訊息進行時間排序...")
    all_messages.sort(key=lambda item: item['timestamp'])

    df_experiments = pd.DataFrame(experiments_flat)
    df_votes = pd.DataFrame(all_votes)
    df_messages = pd.DataFrame(all_messages)
    
    return df_experiments, df_votes, df_messages


if __name__ == "__main__":
    print("🚀 開始從 Firestore 匯出實驗數據...")
    
    # 步驟 1: 建立輸出資料夾
    output_dir = 'analysis/file'
    os.makedirs(output_dir, exist_ok=True)
    print(f"📂 輸出目錄 '{output_dir}' 已準備就緒。")

    # 步驟 2: 從 Firestore 獲取所有巢狀數據
    exported_data = export_all_experiments_data(db)
    
    # 步驟 3: 輸出完整的 JSON 檔案 (給 analyze_data.py 使用)
    json_path = os.path.join(output_dir, 'exported_data.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(exported_data, f, ensure_ascii=False, indent=4)
    print(f"💾 完整的 JSON 數據已儲存至: {json_path}")
    
    # 步驟 4: 扁平化數據並輸出成三個 CSV 檔案
    print("📊 正在將數據轉換為 CSV 格式...")
    df_exp, df_vot, df_msg = flatten_data_for_csv(exported_data)
    
    # 輸出 experiments.csv
    exp_csv_path = os.path.join(output_dir, 'experiments.csv')
    df_exp.to_csv(exp_csv_path, index=False, encoding='utf-8-sig')
    print(f"💾 實驗摘要數據已儲存至: {exp_csv_path}")

    # 輸出 votes.csv
    vot_csv_path = os.path.join(output_dir, 'votes.csv')
    df_vot.to_csv(vot_csv_path, index=False, encoding='utf-8-sig')
    print(f"💾 投票紀錄數據已儲存至: {vot_csv_path}")
    
    # 輸出 messages.csv
    msg_csv_path = os.path.join(output_dir, 'messages.csv')
    df_msg.to_csv(msg_csv_path, index=False, encoding='utf-8-sig')
    print(f"💾 訊息紀錄數據已儲存至: {msg_csv_path}")

    print(f"\n🎉 數據匯出成功！總共匯出了 {len(exported_data)} 組實驗數據。")