# 使用 Python 基礎映像
FROM python:3.9-slim

# 設定環境變數，讓 gunicorn 輸出 log 到 stdout/stderr
ENV PYTHONUNBUFFERED 1
ENV PORT 8080

# 設定工作目錄
WORKDIR /main

# 複製並安裝依賴（利用快取優化）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案內所有檔案。由於 .dockerignore 的存在，analysis/ 資料夾將被跳過
COPY . .

# 暴露埠 8080（Cloud Run 預設）
EXPOSE 8080

# 啟動 Flask 應用 (確保您已在 requirements.txt 中包含 gunicorn)
CMD ["gunicorn", "-b", "0.0.0.0:${PORT}", "main:app"]