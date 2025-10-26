import pandas as pd
import os
import glob # 用於查找所有 .xlsx 檔案

def merge_coded_sheets():
    
    input_dir = 'analysis/final_coding_worksheets'
    output_file = 'analysis/file/coded_messages_FINAL.csv'
    
    # 1. 查找所有你編碼完成的 Excel 檔案
    search_path = os.path.join(input_dir, "*.xlsx")
    all_files = glob.glob(search_path)
    
    if not all_files:
        print(f"❌ 錯誤：在 '{input_dir}' 資料夾中找不到任何 .xlsx 檔案。")
        print("   請確認你已將編碼完成的檔案放回此資料夾。")
        return

    print(f"🔍 找到了 {len(all_files)} 個已編碼的 Excel 檔案，正在合併...")
    
    # 2. 逐一讀取並合併
    df_list = []
    for f in all_files:
        try:
            df = pd.read_excel(f)
            df_list.append(df)
        except Exception as e:
            print(f"  -> 讀取 {f} 時發生錯誤: {e}")
            
    if not df_list:
        print("❌ 錯誤：無法讀取任何 Excel 檔案。")
        return
        
    merged_df = pd.concat(df_list, ignore_index=True)
    
    # 3. 重新按時間排序
    merged_df = merged_df.sort_values(by='timestamp')
    
    # 4. 儲存為最終的 CSV 檔案
    try:
        merged_df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print("\n" + "="*50)
        print(f"🎉 成功合併！")
        print(f"   所有編碼資料已儲存至: {output_file}")
        print("   你現在可以使用這個檔案進行（二）資訊揭露的統計分析了。")
        print("="*50)
    except Exception as e:
        print(f"❌ 儲存最終 CSV 檔案時發生錯誤: {e}")

if __name__ == "__main__":
    merge_coded_sheets()