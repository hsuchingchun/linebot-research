import pandas as pd
import numpy as np
import os
import re

# ======================================================================
# 步驟一：定義你的「編碼簿」和「關鍵詞」
# (來自你的腳本)
# ======================================================================

CODEBOOK = {
    # Shared Cues
    'S_Amy_1': '資歷長 (+)',
    'S_Amy_2': '注重細節 (+)',
    'S_Amy_3': '喜歡西洋棋 (O)',
    'S_Sally_1': '優秀的演講者 (+)',
    'S_Sally_2': '注重細節 (+)',
    'S_Sally_3': '曾在倫敦的財務部門工作兩年 (+)',
    'S_Nancy_1': '會計師資格 (+)',
    'S_Nancy_2': '不記得表揚員工貢獻 (-)',
    'S_Nancy_3': '完成領導課程 (+)',
    # Unshared Cues
    'U_Amy_HR1': '會議遲到 (-)',
    'U_Amy_HR2': '攝影比賽第一名 (O)',
    'U_Amy_Ops1': '不是鼓舞人心的演講者 (-)',
    'U_Amy_Ops2': '擔任調解委員會主席獲得好評 (+)',
    'U_Amy_Mkt1': '冷漠不愛社交 (-)',
    'U_Amy_Mkt2': '完成領導計畫 (+)',
    'U_Sally_HR1': '霸道 (-)',
    'U_Sally_HR2': '公司募捐活動主席 (+)',
    'U_Sally_Ops1': '組織政治高手 (O)',
    'U_Sally_Ops2': '曾擔任財務長志工 (O)',
    'U_Sally_Mkt1': '冷漠不愛社交 (-)',
    'U_Sally_Mkt2': '曾在股票問題擔任重要角色 (+)',
    'U_Nancy_HR1': '注重細節 (+)',
    'U_Nancy_HR2': '公平 (+)',
    'U_Nancy_Ops1': '對組織政治有洞察力 (+)',
    'U_Nancy_Ops2': '審計實務 (+)',
    'U_Nancy_Mkt1': '曾為舉辦退休財務長送別會 (O)',
    'U_Nancy_Mkt2': '豐富的國際差旅和商業諮詢經驗 (+)'
}

KEYWORD_MAP = {
    'S_Amy_1': ['資歷長'],
    'S_Amy_2': ['注重細節', '細節'],
    'S_Amy_3': ['西洋棋'],
    'S_Sally_1': ['演講者', '演講', '口才'],
    'S_Sally_2': ['注重細節', '細節'],
    'S_Sally_3': ['倫敦', '財務部門工作兩年'],
    'S_Nancy_1': ['會計師', '證照'],
    'S_Nancy_2': ['不記得表揚', '不會帶隊', '不會記得表揚同事'],
    'S_Nancy_3': ['領導課程','領導'],
    'U_Amy_HR1': ['遲到'],
    'U_Amy_HR2': ['攝影'],
    'U_Amy_Ops1': ['不是鼓舞人心', '演講者'], 
    'U_Amy_Ops2': ['調解委員會'],
    'U_Amy_Mkt1': ['冷漠', '不愛社交', '不社交'],
    'U_Amy_Mkt2': ['領導課程','領導'],
    'U_Sally_HR1': ['霸道'], 
    'U_Sally_HR2': ['募捐'],
    'U_Sally_Ops1': ['組織政治', '政治高手'],
    'U_Sally_Ops2': ['志工', '財務長志工'],
    'U_Sally_Mkt1': ['冷漠', '不愛社交', '不社交'], 
    'U_Sally_Mkt2': ['股票'],
    'U_Nancy_HR1': ['注重細節', '細節'],
    'U_Nancy_HR2': ['公平'],
    'U_Nancy_Ops1': ['組織政治', '洞察力'],
    'U_Nancy_Ops2': ['審計'],
    'U_Nancy_Mkt1': ['送別會'],
    'U_Nancy_Mkt2': ['國際差旅', '商業諮詢', '國際視野'] 
}

def sanitize_filename(name):
    """
    清理檔案名稱，移除會導致儲存錯誤的特殊字元。
    """
    if pd.isna(name):
        return "Unnamed_Group"
    name_str = str(name)
    name_str = re.sub(r'[\\/*?:"<>|()\[\]]', '', name_str)
    name_str = re.sub(r'[\s_]+', '_', name_str).strip('-_')
    return name_str

# ======================================================================
# 步驟二：整併後的主程式
# ======================================================================

def create_precoded_worksheets():
    
    input_file = 'analysis/messages.csv'
    output_dir = 'analysis/coding_worksheets'

    # 1. 建立輸出資料夾
    os.makedirs(output_dir, exist_ok=True)
    
    # 2. 讀取 CSV
    try:
        df = pd.read_csv(input_file)
        df['text'] = df['text'].astype(str)
    except FileNotFoundError:
        print(f"❌ 錯誤：找不到檔案 '{input_file}'。")
        print("   請確認 'analysis/messages.csv' 檔案存在。")
        return
    except Exception as e:
        print(f"❌ 讀取 CSV 時發生錯誤: {e}")
        return
        
    print(f"✅ 成功讀取 {input_file}，共 {len(df)} 則訊息。")

    # 3. 準備編碼欄位
    all_codes = list(CODEBOOK.keys())
    
    # 4. (來自腳本一) 新增欄位，並初始化為 0
    print(f"✅ 正在新增 27 個編碼欄位 (S_Amy_1 ... U_Nancy_Mkt2)...")
    for col in all_codes:
        if col not in df.columns:
            df[col] = 0

    # 5. (來自腳本一) 執行「關鍵詞自動預編碼」
    print("🤖 正在執行關鍵詞自動預編碼 (First Pass)...")
    coded_count = 0
    for index, row in df.iterrows():
        # 只編碼人類的發言
        if row['from'] == 'user':
            text = str(row['text']).lower() # 轉換為小寫以便比對
            
            for code, keywords in KEYWORD_MAP.items():
                for keyword in keywords:
                    if keyword.lower() in text:
                        # 基礎否定詞檢查
                        if "不" not in text and "沒有" not in text:
                            df.at[index, code] = 1
                            coded_count += 1
                            break # 找到一個 code 的匹配就跳到下一個 code
    
    print(f"🤖 自動預編碼完成，初步標記了 {coded_count} 處資訊點。")

    # 6. (來自腳本二) 按 group_id 分組並儲存
    print(f"🔄 正在將預編碼的資料拆分為獨立的 Excel 工作表...")
    
    base_cols = ['group_id', 'group_name', 'user_id', 'from', 'text', 'timestamp']
    existing_base_cols = [col for col in base_cols if col in df.columns]
    
    # 最終輸出的欄位順序：基礎欄位 + 所有編碼欄位
    final_output_cols = existing_base_cols + all_codes
    
    grouped = df.groupby('group_id')
    created_files_count = 0
    
    for group_id, group_df in grouped:
        
        # 準備這個群組的 DataFrame (它已經被預先編碼了)
        # 我們只選擇需要的欄位
        worksheet_df = group_df[final_output_cols].copy()
        
        # 取得群組名稱並清理
        try:
            group_name = group_df['group_name'].iloc[0]
            sane_name = sanitize_filename(group_name)
            output_filename = os.path.join(output_dir, f"{sane_name}.xlsx")
        except Exception:
            output_filename = os.path.join(output_dir, f"{group_id}.xlsx")

        # 儲存為 Excel 檔案
        try:
            worksheet_df.to_excel(output_filename, index=False, engine='openpyxl')
            print(f"  -> 已產生編碼工作表: {output_filename}")
            created_files_count += 1
        except Exception as e:
            print(f"  -> 儲存 {output_filename} 時發生錯誤: {e}")
            
    print("\n" + "="*50)
    print(f"🎉 成功！共產生了 {created_files_count} 份獨立的【預編碼】工作表。")
    print(f"   請至 '{output_dir}' 資料夾開始你的編碼工作。")
    print("="*50)
    print("\n下一步行動：")
    print("1. 請用 Excel 或 Google Sheets 打開這些 .xlsx 檔案。")
    print("2. 逐行【手動複核】，修正 AI 的錯誤標記 (例如否定句判斷錯誤)。")
    print("3. 逐行【手動補充】，AI 錯過的「本質提及」(Gist Mentions)。")
    print("4. 編碼完成後，請執行 `2_merge_coded_sheets.py` 腳本來合併它們。")


if __name__ == "__main__":
    create_precoded_worksheets()