import pandas as pd
import numpy as np
import os
from collections import Counter
import statsmodels.api as sm
from statsmodels.formula.api import ols
from scipy.stats import chi2_contingency

# --- 分析前的準備工作 (維持不變) ---
ROLE_TO_CHOICE_MAPPING = {
    "人資長": "Sally",
    "營運長": "Nancy",
    "行銷長": "Sally",
}

# ====== 階段一：指標計算函式 (維持不變) ======
# ... (您所有的 calculate_... 函式都維持原樣，此處省略以保持簡潔)
def calculate_initial_preference(row):
    votes = row['main_votes_data']
    initial_choices = [v.get('initial_choice') for v in votes if v.get('initial_choice')]
    if not initial_choices: return 0
    counts = Counter(initial_choices)
    if counts and counts.most_common(1)[0][1] >= 2: return 1
    return 0

def calculate_objective_quality(row):
    votes = row['main_votes_data']
    final_choices = [v.get('final_choice') for v in votes if v.get('final_choice')]
    if 'Nancy' in final_choices: return 1
    return 0

def calculate_speaking_balance(row):
    messages = row['main_messages_data']
    char_counts = {}
    total_chars = 0
    for msg in messages:
        user = msg.get('user_id', 'AI_ASSISTANT')
        text = msg.get('text', '')
        char_counts[user] = char_counts.get(user, 0) + len(text)
        total_chars += len(text)
    if total_chars == 0: return 0
    proportions = [count / total_chars for count in char_counts.values()]
    return np.std(proportions) if proportions else 0

def calculate_consensus_ratio(row):
    reached = row.get('consensus_reached_count', 0)
    failed = row.get('consensus_failed_count', 0)
    total = reached + failed
    return reached / total if total > 0 else 0

def calculate_role_consistency(row):
    votes = row['main_votes_data']
    consistent_count = 0
    for v in votes:
        position, initial_choice = v.get('position'), v.get('initial_choice')
        if position and initial_choice:
            if ROLE_TO_CHOICE_MAPPING.get(position) == initial_choice:
                consistent_count += 1
    return consistent_count


# ====== 階段二：主分析流程 ======
def main():
    # 1. 讀取並載入數據
    try:
        df = pd.read_json('analysis/exported_data.json')
    except FileNotFoundError:
        print("❌ 錯誤：找不到 'analysis/exported_data.json'。請先執行 'export_firestore_data.py'。")
        return

    print("📊 數據載入成功，開始計算分析指標...")
    
    # 2-A: 拆解自變項
    factor_mapping = {
        '混合型AI': {'integration': 'Y', 'inquiry': 'Y'},
        '整合型AI': {'integration': 'Y', 'inquiry': 'N'},
        '探究型AI': {'integration': 'N', 'inquiry': 'Y'},
        '無介入AI': {'integration': 'N', 'inquiry': 'N'}
    }
    df['integration'] = df['bot_role'].map(lambda x: factor_mapping.get(x, {}).get('integration'))
    df['inquiry'] = df['bot_role'].map(lambda x: factor_mapping.get(x, {}).get('inquiry'))

    # 2-B: 計算依變項指標
    # ... (計算指標的程式碼維持不變)
    df['initial_preference'] = df.apply(calculate_initial_preference, axis=1)
    df['objective_quality'] = df.apply(calculate_objective_quality, axis=1)
    df['speaking_turns'] = df['main_messages_data'].apply(len)
    df['speaking_balance_std'] = df.apply(calculate_speaking_balance, axis=1)
    df['consensus_ratio'] = df.apply(calculate_consensus_ratio, axis=1)
    df['role_consistency'] = df.apply(calculate_role_consistency, axis=1)
    df.rename(columns={'total_duration_seconds': 'total_discussion_time', 'final_decision_count': 'final_decision_attempts'}, inplace=True)
    
    print("✅ 所有指標計算完成！")

    # 3: 匯出 CSV
    # ... (匯出 CSV 的程式碼維持不變)
    print("\n🚀 正在將計算結果匯出至 CSV...")
    output_columns = ['group_id','group_name', 'bot_role', 'integration', 'inquiry', 'objective_quality', 'initial_preference', 'role_consistency', 'total_discussion_time', 'speaking_turns', 'final_decision_attempts', 'consensus_ratio', 'speaking_balance_std']
    results_df = df[output_columns].copy()
    output_dir = 'analysis'
    os.makedirs(output_dir, exist_ok=True)
    output_csv_path = os.path.join(output_dir, 'analysis_results.csv')
    results_df.to_csv(output_csv_path, index=False, encoding='utf-8-sig')
    print(f"✅ 分析結果已成功匯出至: {output_csv_path}")


    # ======================================================================
    # ✨ 步驟 4: 執行統計分析 (輸出格式已修改為 DataFrame)
    # ======================================================================
    print("\n" + "="*50)
    print("🔬 開始執行組間差異統計分析 (雙因子 ANOVA & 卡方檢定)")
    print("="*50 + "\n")

    # --- 類別變項分析 (卡方檢定) ---
    print("\n--- (一) 決策品質 (類別變項) ---")
    for var in ['initial_preference', 'objective_quality']:
        print(f"\n===== 分析依變項: {var} =====")
        contingency_table = pd.crosstab(df['bot_role'], df[var])
        
        print("\n[列聯表 (Contingency Table)]")
        print(contingency_table)

        if not contingency_table.empty:
            chi2, p, dof, ex = chi2_contingency(contingency_table)
            
            # ✨ 使用 DataFrame 呈現統計結果
            stats_df = pd.DataFrame({
                'Statistic': [f'Chi-Square ({dof} df)'],
                'Value': [f'{chi2:.2f}'],
                'P-value': [f'{p:.4f}'],
                'Significant (p < .05)': ['Yes' if p < 0.05 else 'No']
            })
            print("\n[統計檢定結果]")
            print(stats_df.to_string(index=False))
        else:
            print("\n[統計檢定結果]")
            print("數據不足，無法執行卡方檢定。")


    # --- 雙因子變異數分析 ---
    continuous_vars = [
        'total_discussion_time', 'speaking_turns', 'consensus_ratio',
        'final_decision_attempts', 'speaking_balance_std', 'role_consistency'
    ]
    print("\n\n--- (雙因子變異數分析) ---")
    
    print("\n[數據分佈情況檢查]")
    distribution_table = pd.crosstab(df['integration'], df['inquiry'])
    print(distribution_table)
    is_design_complete = df['integration'].nunique() >= 2 and df['inquiry'].nunique() >= 2
    
    if not is_design_complete:
        print("\n❌ 警告: 您的數據不滿足完整的 2x2 設計。無法執行雙因子 ANOVA。")
        return

    for var in continuous_vars:
        if var in df.columns and pd.api.types.is_numeric_dtype(df[var]):
            print(f"\n\n===== 分析依變項: {var} =====")
            
            formula = f"{var} ~ C(integration) + C(inquiry) + C(integration):C(inquiry)"
            
            try:
                if df[var].notna().sum() < 4:
                    raise ValueError("有效數據點不足 (少于 4 個)。")

                model = ols(formula, data=df).fit()
                anova_table = sm.stats.anova_lm(model, typ=2)
                
                # ✨ 格式化 ANOVA 表格後再印出
                formatted_anova_table = anova_table.copy()
                formatted_anova_table['F'] = formatted_anova_table['F'].map('{:.2f}'.format)
                formatted_anova_table['PR(>F)'] = formatted_anova_table['PR(>F)'].map('{:.4f}'.format)
                print("\n[ANOVA Table]")
                print(formatted_anova_table)

                # ✨ 使用 DataFrame 呈現結果解讀
                p_integration = anova_table.loc['C(integration)', 'PR(>F)']
                p_inquiry = anova_table.loc['C(inquiry)', 'PR(>F)']
                p_interaction = anova_table.loc['C(integration):C(inquiry)', 'PR(>F)']
                
                summary_data = [
                    {'Effect': 'Integration Main Effect', 'P-value': p_integration, 'Significant (p < .05)': 'Yes' if p_integration < 0.05 else 'No'},
                    {'Effect': 'Inquiry Main Effect', 'P-value': p_inquiry, 'Significant (p < .05)': 'Yes' if p_inquiry < 0.05 else 'No'},
                    {'Effect': 'Interaction Effect', 'P-value': p_interaction, 'Significant (p < .05)': 'Yes' if p_interaction < 0.05 else 'No'}
                ]
                summary_df = pd.DataFrame(summary_data)
                summary_df['P-value'] = summary_df['P-value'].map('{:.4f}'.format)

                print("\n[結果解讀]")
                print(summary_df.to_string(index=False))

            except Exception as e:
                print(f"\n[錯誤]")
                print(f"執行 ANOVA 時發生錯誤: {e}")
                print("   這通常意味著此變項在某些實驗條件下沒有足夠的數據。")
        else:
            print(f"\n變項: {var} - 因數據非數值或不存在，跳過檢定。")


if __name__ == "__main__":
    main()