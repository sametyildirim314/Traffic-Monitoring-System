# Canlı Trafik İzleme Sistemi

## 📌 Proje Özeti

Bu proje, Ankara’nın kritik kavşaklarındaki trafik verilerini analiz ederek gerçek zamanlı olarak görselleştiren bir **canlı trafik izleme sistemidir**. Sistem, Python, Node.js ve HTML/JavaScript teknolojilerini bir araya getirerek trafik yoğunluğu, ortalama hız, bekleme süresi ve araç sayısı gibi verileri işler ve bulut tabanlı bir arayüzde kullanıcıya sunar.

### 🔗 Canlı izleme ve uyarı sistemi AWS IoT Core ile desteklenmektedir.

---

## 🧪 Kullanılan Teknolojiler

| Katman       | Teknoloji                                  |
|--------------|---------------------------------------------|
| Backend      | Node.js (Express.js), MQTT                 |
| Veri İşleme  | Python (AWS IoT bağlantılı analiz scripti) |
| Frontend     | HTML, CSS, JavaScript (Leaflet, Chart.js)  |
| Veritabanı   | SQLite                                      |
| Bulut        | AWS IoT Core                                |

---

## 🛠️ Sistem Mimarisi ve Çalışma Prensibi

- **Python script**:
  - Her 20 saniyede bir trafik verilerini analiz eder.
  - Verileri `SQLite` veritabanına kaydeder.
  - Aynı anda verileri **AWS IoT Core** servisine MQTT üzerinden gönderir.

- **Node.js Backend**:
  - Hem REST API hem MQTT üzerinden veri sunar.
  - Frontend ile gerçek zamanlı veri alışverişini sağlar.

- **Frontend**:
  - Trafik verilerini çekerek harita üzerinde ve tablolar halinde kullanıcıya sunar.
  - **Leaflet.js**: Anlık trafik haritası.
  - **Chart.js**: Yoğunluk ve hız görselleştirmesi.

---

## 📦 Özellikler

- 🔴 Gerçek zamanlı trafik yoğunluğu ve uyarılar
- 🗺️ Harita tabanlı trafik görüntüleme
- 📈 Grafik ve tablo görselleriyle özet veriler
- 🕓 Geçmiş trafik verilerini sorgulama
- 📤 JSON formatında veri dışa aktarımı
- 📡 MQTT üzerinden veri mesajlaşması

---

## 🚀 Kurulum

```bash
git clone https://github.com/kullaniciadi/traffic-monitoring-system.git
cd traffic-monitoring-system
npm install
python3 trafik_analiz.py
node server.js
