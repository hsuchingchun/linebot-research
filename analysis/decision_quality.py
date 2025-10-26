import pandas as pd
import numpy as np
import json
import os
from collections import defaultdict, Counter

def calculate_objective_metrics(row):
    """
    針對單一一列 (一個實驗組) 的數據，計算所有客觀指標。
    """
    messages = row.get('main_messages_data', [])
    votes = row.get('main_votes_data', [])

    # --- 指標 (一) 決策品質 ---
    
    initial_preference_code = 0
    initial_choices = [v.get('initial_choice') for v in votes if v.get('initial_choice')]
    if initial_choices:
        choice_counts = Counter(initial_choices)
        if choice_counts and choice_counts.most_common(1)[0][1] >= 2:
            initial_preference_code = 1
            
    objective_quality_code = 0
    final_choices = {v.get('final_choice') for v in votes if v.get('final_choice')}
    if 'Nancy' in final_choices:
        objective_quality_code = 1

    
    # --- 指標 (四) 客觀過程指標 ---
    
    ai_total_utterances = 0
    human_total_utterances = 0
    ai_total_chars = 0
    human_total_chars = 0
    char_counts_by_user = defaultdict(int)

    for msg in messages:
        text = msg.get('text', '')
        if msg.get('from') == 'assistant':
            ai_total_utterances += 1
            ai_total_chars += len(text)
        elif msg.get('from') == 'user':
            human_total_utterances += 1
            human_total_chars += len(text)
            user_id = msg.get('user_id')
            if user_id:
                char_counts_by_user[user_id] += len(text)

    total_discussion_cycles = ai_total_utterances
    
    reached = row.get('consensus_reached_count', 0)
    failed = row.get('consensus_failed_count', 0)
    total_consensus_checks = reached + failed
    consensus_ratio = reached / total_consensus_checks if total_consensus_checks > 0 else 0

    ai_avg_length = ai_total_chars / ai_total_utterances if ai_total_utterances > 0 else 0
    human_avg_length = human_total_chars / human_total_utterances if human_total_utterances > 0 else 0

    # ======================================================================
    # 計算人類貢獻平衡性 (使用變異係數 CV)
    all_member_ids = {vote['user_id'] for vote in votes if 'user_id' in vote}
    if not all_member_ids and char_counts_by_user:
        all_member_ids = set(char_counts_by_user.keys())
        
    final_char_counts = [char_counts_by_user.get(uid, 0) for uid in all_member_ids]
    
    human_contribution_cv = 0.0 # 預設為 0 (完全均衡)
    if final_char_counts:
        mean_chars = np.mean(final_char_counts)
        sd_chars = np.std(final_char_counts)
        
        # 關鍵計算：CV = SD / Mean
        # 增加保護：只有在平均值大於 0 時才計算，避免除以零
        if mean_chars > 0:
            human_contribution_cv = sd_chars / mean_chars
        # 如果 mean_chars 是 0 (所有人都沒發言), sd_chars 也會是 0, CV 自然是 0
            
    # ======================================================================

    
    # --- 回傳所有指標 ---
    return pd.Series({
        'initial_preference': initial_preference_code,
        'objective_quality': objective_quality_code,
        'total_discussion_cycles': total_discussion_cycles,
        'consensus_ratio': consensus_ratio,
        'ai_average_utterance_length': ai_avg_length,
        'human_average_utterance_length': human_avg_length,
        'human_contribution_balance_cv': human_contribution_cv # ✨ 名稱已更新
    })

def main():
    """
    主執行流程：載入、計算、儲存
    """
    
    input_file = 'analysis/file/exported_data.json' # ✨ 已修正路徑
    output_dir = 'analysis/file'
    output_file = os.path.join(output_dir, 'decision_results.csv')

    os.makedirs(output_dir, exist_ok=True)

    # 1. 載入 JSON 數據
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"✅ 成功從 {input_file} 載入 {len(data)} 筆實驗數據。")
    except FileNotFoundError:
        print(f"❌ 錯誤：找不到檔案 '{input_file}'。")
        print("   請確認你的 exported_data.json 檔案在 'analysis' 資料夾中。")
        return
    except json.JSONDecodeError:
        print(f"❌ 錯誤：{input_file} 檔案格式錯誤，不是有效的 JSON。")
        return

    df = pd.DataFrame(data)

    # 2. 拆解 2x2 自變項
    print("🔧 正在拆解 2x2 自變項 (integration, inquiry)...")
    factor_mapping = {
        '混合型AI': {'integration': 'Y', 'inquiry': 'Y'},
        '整合型AI': {'integration': 'Y', 'inquiry': 'N'},
        '探究型AI': {'integration': 'N', 'inquiry': 'Y'},
        '無介入AI': {'integration': 'N', 'inquiry': 'N'}
    }
    df['integration'] = df['bot_role'].map(lambda x: factor_mapping.get(x, {}).get('integration'))
    df['inquiry'] = df['bot_role'].map(lambda x: factor_mapping.get(x, {}).get('inquiry'))

    # 3. 計算所有客觀指標
    print("📊 正在計算所有客觀指標...")
    metrics_df = df.apply(calculate_objective_metrics, axis=1)
    df = pd.concat([df, metrics_df], axis=1)
    print("✅ 所有指標計算完成！")

    # 4. 整理並匯出 CSV
    df.rename(columns={
        'total_duration_seconds': 'total_discussion_time',
        'final_decision_count': 'final_decision_attempts'
    }, inplace=True)

    final_columns = [
        'group_id', 
        'group_name', 
        'bot_role',
        'integration',
        'inquiry',
        'objective_quality',
        'initial_preference',
        'total_discussion_time',        
        'total_discussion_cycles',      
        'final_decision_attempts',      
        'consensus_ratio',              
        'ai_average_utterance_length',  
        'human_average_utterance_length',
        'human_contribution_balance_cv' # ✨ 名稱已更新
    ]
    
    existing_columns = [col for col in final_columns if col in df.columns]
    output_df = df[existing_columns].copy()

    # (可選) 強制轉換型態，讓 CSV 更美觀
    if 'objective_quality' in output_df.columns:
        output_df['objective_quality'] = output_df['objective_quality'].astype(int)
    if 'initial_preference' in output_df.columns:
        output_df['initial_preference'] = output_df['initial_preference'].astype(int)
    if 'total_discussion_cycles' in output_df.columns:
        output_df['total_discussion_cycles'] = output_df['total_discussion_cycles'].astype(int)

    output_df.to_csv(output_file, index=False, encoding='utf-8-sig')
    
    print("\n" + "="*50)
    print(f"🎉 分析結果已成功匯出至: {output_file}")
    print("="*50)
    print("\n[CSV 檔案內容預覽]")
    # 調整 Pandas 顯示格式，讓小數點更清晰
    pd.set_option('display.float_format', '{:.4f}'.format)
    print(output_df.head())


if __name__ == "__main__":
    main()