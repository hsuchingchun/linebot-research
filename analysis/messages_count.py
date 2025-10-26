import pandas as pd
import os

# ======================================================================
# 步驟一：定義你的「編碼簿」代碼
# (來自你的 calculate_information_disclosure 函式)
# ======================================================================
SHARED_COLS = ['S_Amy_1', 'S_Amy_2', 'S_Amy_3', 
               'S_Sally_1', 'S_Sally_2', 'S_Sally_3', 
               'S_Nancy_1', 'S_Nancy_2', 'S_Nancy_3']

PRIVATE_COLS = ['U_Amy_HR1', 'U_Amy_HR2', 'U_Amy_Ops1', 'U_Amy_Ops2', 'U_Amy_Mkt1', 'U_Amy_Mkt2',
                'U_Sally_HR1', 'U_Sally_HR2', 'U_Sally_Ops1', 'U_Sally_Ops2', 'U_Sally_Mkt1', 'U_Sally_Mkt2',
                'U_Nancy_HR1', 'U_Nancy_HR2', 'U_Nancy_Ops1', 'U_Nancy_Ops2', 'U_Nancy_Mkt1', 'U_Nancy_Mkt2']

# 總私有資訊數量 (用於計算比例)
TOTAL_PRIVATE_INFO_COUNT = 18.0

# ======================================================================
# 步驟二：定義計算函式 (稍作優化)
# ======================================================================
def calculate_information_disclosure(coded_messages_df):
    """
    從已編碼的 messages DataFrame 中計算資訊揭露指標 (按 group_id 聚合)。
    返回一個以 group_id 為索引的 DataFrame。
    """
    
    # 確保所有必要的編碼欄位都存在，若不存在則填 0
    all_code_cols = SHARED_COLS + PRIVATE_COLS
    for col in all_code_cols:
        if col not in coded_messages_df.columns:
            coded_messages_df[col] = 0
            print(f"警告：欄位 '{col}' 在輸入的 CSV 中不存在，已自動補 0。")

    # 篩選人類成員的發言
    user_messages = coded_messages_df[coded_messages_df['from'] == 'user'].copy()
    
    # 將所有編碼欄位轉換為數值型態 (防禦性程式設計)
    user_messages[all_code_cols] = user_messages[all_code_cols].apply(pd.to_numeric, errors='coerce').fillna(0)

    # 按 group_id 分組
    grouped = user_messages.groupby('group_id')
    
    # 計算每個 code 的總提及「次數」 (包含重複)
    total_mentions = grouped[all_code_cols].sum()
    
    # 計算每個 code 是否「獨特」提及 (提及 >= 1 次即算 1)
    unique_mentions = total_mentions.gt(0).astype(int)
    
    # 建立最終結果 DataFrame
    disclosure_results = pd.DataFrame(index=total_mentions.index)
    
    # 計算 (二)-1: 共同資訊討論次數
    disclosure_results['shared_info_mention_count'] = total_mentions[SHARED_COLS].sum(axis=1)
    
    # 計算 (二)-2: 私有資訊揭露次數
    disclosure_results['unshared_info_mention_count'] = total_mentions[PRIVATE_COLS].sum(axis=1)
    
    # 計算 (二)-3: 私有資訊揭露比例 (核心指標)
    disclosure_results['unshared_info_ratio'] = unique_mentions[PRIVATE_COLS].sum(axis=1) / TOTAL_PRIVATE_INFO_COUNT
    
    return disclosure_results

# ======================================================================
# 步驟三：主執行流程
# ======================================================================
def main():
    
    # --- 輸入檔案路徑 ---
    coded_messages_file = 'analysis/file/coded_messages_FINAL.csv'
    experiments_file = 'analysis/file/experiments.csv' # 包含 group_id, group_name, bot_role
    
    # --- 輸出檔案路徑 ---
    output_dir = 'analysis/file'
    output_file = os.path.join(output_dir, 'information_disclosure_results.csv')

    # --- 讀取輸入檔案 ---
    try:
        coded_messages_df = pd.read_csv(coded_messages_file)
        print(f"✅ 成功讀取已編碼訊息: {coded_messages_file}")
    except FileNotFoundError:
        print(f"❌ 錯誤：找不到 '{coded_messages_file}'。")
        print("   請確認你已完成手動編碼並將檔案儲存於正確路徑。")
        return
    except Exception as e:
        print(f"❌ 讀取 {coded_messages_file} 時發生錯誤: {e}")
        return
        
    try:
        experiments_df = pd.read_csv(experiments_file)
        # 只保留需要的欄位，避免合併後重複
        experiments_df = experiments_df[['group_id', 'group_name', 'bot_role']].copy()
        print(f"✅ 成功讀取實驗資訊: {experiments_file}")
    except FileNotFoundError:
        print(f"❌ 錯誤：找不到 '{experiments_file}'。")
        print("   請確認你的 experiments.csv 檔案在 'analysis' 資料夾中。")
        return
    except Exception as e:
        print(f"❌ 讀取 {experiments_file} 時發生錯誤: {e}")
        return

    # --- 執行計算 ---
    print("\n📊 正在計算資訊揭露指標...")
    disclosure_results_df = calculate_information_disclosure(coded_messages_df)
    print("✅ 資訊揭露指標計算完成！")

    # --- 合併結果與實驗條件 ---
    print("🔄 正在合併計算結果與實驗條件...")
    # 使用 'inner' 合併，確保只保留兩個檔案中都存在的 group_id
    final_df = experiments_df.merge(disclosure_results_df, on='group_id', how='inner')

    # --- 加入 2x2 自變項拆解 ---
    print("🔧 正在拆解 2x2 自變項 (integration, inquiry)...")
    factor_mapping = {
        '混合型AI': {'integration': 'Y', 'inquiry': 'Y'},
        '整合型AI': {'integration': 'Y', 'inquiry': 'N'},
        '探究型AI': {'integration': 'N', 'inquiry': 'Y'},
        '無介入AI': {'integration': 'N', 'inquiry': 'N'}
    }
    # 確保 bot_role 欄位存在
    if 'bot_role' in final_df.columns:
        final_df['integration'] = final_df['bot_role'].map(lambda x: factor_mapping.get(x, {}).get('integration'))
        final_df['inquiry'] = final_df['bot_role'].map(lambda x: factor_mapping.get(x, {}).get('inquiry'))
    else:
        print("❌ 錯誤：合併後的 DataFrame 中缺少 'bot_role' 欄位，無法拆解自變項。")
        return

    # --- 整理最終輸出欄位 ---
    output_columns = [
        'group_id', 
        'group_name', 
        'bot_role', 
        'integration', 
        'inquiry',
        'shared_info_mention_count',
        'unshared_info_mention_count',
        'unshared_info_ratio'
    ]
    # 再次檢查欄位是否存在
    existing_output_columns = [col for col in output_columns if col in final_df.columns]
    final_output_df = final_df[existing_output_columns]

    # --- 儲存最終結果 ---
    try:
        os.makedirs(output_dir, exist_ok=True) # 確保輸出目錄存在
        final_output_df.to_csv(output_file, index=False, encoding='utf-8-sig')
        
        print("\n" + "="*50)
        print(f"🎉 成功！資訊揭露分析結果已儲存至:")
        print(f"   {output_file}")
        print("="*50)
        print("\n[CSV 檔案內容預覽]")
        pd.set_option('display.float_format', '{:.4f}'.format)
        print(final_output_df.head())
        
    except Exception as e:
        print(f"❌ 儲存最終 CSV 檔案時發生錯誤: {e}")

if __name__ == "__main__":
    main()