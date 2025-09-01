# 使用 Python 基礎映像
FROM python:3.9-slim

# 設定工作目錄，命名須與主要程式碼檔案相同 (main.py->/main)
WORKDIR /main

# 複製專案內所有檔案
COPY . .

# 安裝依賴
RUN pip install --no-cache-dir -r requirements.txt

# 暴露埠 8080（Cloud Run 預設）
EXPOSE 8080

# 啟動 Flask 應用，命名須與主要程式碼檔案相同 (main.py->"main:app")
CMD ["gunicorn", "-b", "0.0.0.0:8080", "main:app"]
