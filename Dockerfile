# Python tabanlı resmi hafif imajı kullanıyoruz
FROM python:3.11-slim

# Sistem paketlerini güncelleyip FFmpeg'i kuruyoruz (proxy akışı için zorunludur)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Çalışma dizinini belirliyoruz
WORKDIR /app

# Bağımlılık dosyasını kopyalayıp yüklüyoruz
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Tüm proje dosyalarını kopyalıyoruz
COPY . .

# Render'ın dinamik olarak atayacağı portu tanımlıyoruz
ENV PORT=5000

# Uygulamayı Gunicorn ile yüksek performanslı ve çoklu thread desteğiyle başlatıyoruz
CMD gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 app:app
