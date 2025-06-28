# aws_iot_traffic_analyzer.py

from dotenv import load_dotenv
load_dotenv()
import asyncio
import json
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
import time
import sqlite3
import os
from threading import Thread
import ssl

# AWS IoT Core için gerekli kütüphaneler
from awscrt import io, mqtt, auth, http
from awsiot import mqtt_connection_builder
import uuid

# Logging yapılandırması
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class TrafficData:
    intersection_id: int
    name: str
    lat: float
    lng: float
    density: float
    avg_speed: int
    wait_time: int
    vehicle_count: int
    status: str
    timestamp: datetime

class AWSIoTTrafficAnalyzer:
    def __init__(self, google_maps_api_key: str = None):
        self.api_key = google_maps_api_key
        self.intersections = [
            {"id": 1, "name": "Kızılay Meydanı", "lat": 39.9208, "lng": 32.8541, "type": "major"},
            {"id": 2, "name": "Tandoğan Kavşağı", "lat": 39.9347, "lng": 32.8197, "type": "major"},
            {"id": 3, "name": "Kuğulu Park Kavşağı", "lat": 39.9019, "lng": 32.8597, "type": "medium"},
            {"id": 4, "name": "Tunalı Hilmi Caddesi", "lat": 39.9089, "lng": 32.8486, "type": "medium"},
            {"id": 5, "name": "Çankaya Caddesi", "lat": 39.9153, "lng": 32.8625, "type": "medium"},
            {"id": 6, "name": "Atatürk Bulvarı - Sıhhiye", "lat": 39.9294, "lng": 32.8597, "type": "major"},
            {"id": 7, "name": "GMK Bulvarı - Maltepe", "lat": 39.9256, "lng": 32.8378, "type": "medium"},
            {"id": 8, "name": "Bahçelievler Kavşağı", "lat": 39.9133, "lng": 32.8264, "type": "medium"}
        ]
        
        # AWS IoT Core ayarları
        self.aws_iot_endpoint = os.getenv('AWS_IOT_ENDPOINT')
        self.cert_path = os.getenv('AWS_IOT_CERT_PATH', './certs/device.pem.crt')
        self.private_key_path = os.getenv('AWS_IOT_PRIVATE_KEY_PATH', './certs/private.pem.key')
        self.ca_path = os.getenv('AWS_IOT_CA_PATH', './certs/Amazon-root-CA-1.pem')
        self.thing_name = os.getenv('AWS_IOT_THING_NAME', 'AnkaraTrafficSystem')
        
        self.connection = None
        self.setup_database()
        self.setup_aws_iot_connection()
        
    def setup_database(self):
        """SQLite veritabanını kurulum"""
        conn = sqlite3.connect('traffic_data.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS traffic_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                intersection_id INTEGER,
                name TEXT,
                lat REAL,
                lng REAL,
                density REAL,
                avg_speed INTEGER,
                wait_time INTEGER,
                vehicle_count INTEGER,
                status TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                aws_message_id TEXT
            )
        ''')
        
        # AWS IoT mesaj logları için tablo
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS aws_iot_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT,
                message TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("Veritabanı hazırlandı")

    def setup_aws_iot_connection(self):
        """AWS IoT Core bağlantısını kur"""
        try:
            if not all([self.aws_iot_endpoint, os.path.exists(self.cert_path), 
                       os.path.exists(self.private_key_path), os.path.exists(self.ca_path)]):
                logger.warning("AWS IoT sertifikaları bulunamadı, yerel MQTT kullanılacak")
                return False

            # Event loop ve host resolver
            event_loop_group = io.EventLoopGroup(1)
            host_resolver = io.DefaultHostResolver(event_loop_group)
            client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

            # MQTT bağlantısı oluştur
            self.connection = mqtt_connection_builder.mtls_from_path(
                endpoint=self.aws_iot_endpoint,
                cert_filepath=self.cert_path,
                pri_key_filepath=self.private_key_path,
                client_bootstrap=client_bootstrap,
                ca_filepath=self.ca_path,
                client_id=self.thing_name,
                clean_session=False,
                keep_alive_secs=6
            )

            logger.info("AWS IoT Core bağlantısı hazırlandı")
            return True

        except Exception as e:
            logger.error(f"AWS IoT bağlantı kurulumu hatası: {e}")
            return False

    def connect_to_aws_iot(self):
        """AWS IoT Core'a bağlan"""
        try:
            if not self.connection:
                return False

            connect_future = self.connection.connect()
            connect_future.result()
            logger.info("✅ AWS IoT Core'a başarıyla bağlandı")

            # Komut topic'ine abone ol
            subscribe_future, packet_id = self.connection.subscribe(
                topic=f"ankara-traffic/commands/{self.thing_name}",
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self.on_aws_message_received
            )
            subscribe_future.result()
            logger.info(f"📡 AWS IoT topic'ine abone olundu: ankara-traffic/commands/{self.thing_name}")
            return True

        except Exception as e:
            logger.error(f"AWS IoT bağlantı hatası: {e}")
            return False

    def on_aws_message_received(self, topic, payload, dup, qos, retain, **kwargs):
        """AWS IoT'den mesaj alındığında çalışır"""
        try:
            message = json.loads(payload.decode('utf-8'))
            logger.info(f"📨 AWS IoT'den mesaj alındı: {topic}")
            logger.info(f"💬 Mesaj içeriği: {message}")
            
            # Mesajı veritabanına kaydet
            self.save_aws_message_to_db(topic, message)
            
            # Özel komutları işle
            if message.get('command') == 'update_analysis':
                logger.info("🔄 AWS'den analiz güncelleme komutu alındı")
                # Hemen analiz yap ve gönder
                Thread(target=self.immediate_analysis).start()
                
        except Exception as e:
            logger.error(f"AWS mesaj işleme hatası: {e}")

    def save_aws_message_to_db(self, topic: str, message: dict):
        """AWS IoT mesajını veritabanına kaydet"""
        try:
            conn = sqlite3.connect('traffic_data.db')
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO aws_iot_messages (topic, message, status)
                VALUES (?, ?, ?)
            ''', (topic, json.dumps(message), 'received'))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"AWS mesaj kaydetme hatası: {e}")

    def publish_to_aws_iot(self, topic: str, message: dict):
        """AWS IoT Core'a mesaj gönder"""
        try:
            if not self.connection:
                logger.warning("AWS IoT bağlantısı yok, mesaj gönderilemedi")
                return False

            message_json = json.dumps(message, ensure_ascii=False, default=str)
            
            publish_future, packet_id = self.connection.publish(
                topic=topic,
                payload=message_json,
                qos=mqtt.QoS.AT_LEAST_ONCE
            )
            
            publish_future.result()
            logger.info(f"📤 AWS IoT'ye mesaj gönderildi: {topic}")
            
            # Başarılı gönderimi veritabanına kaydet
            self.save_aws_message_to_db(topic, {"status": "published", "message": message})
            return True
            
        except Exception as e:
            logger.error(f"AWS IoT mesaj gönderme hatası: {e}")
            return False

    def calculate_traffic_density(self, intersection_type: str) -> float:
        """Zaman bazlı trafik yoğunluğu hesaplama"""
        current_hour = datetime.now().hour
        current_minute = datetime.now().minute
        
        # Hafta içi/hafta sonu kontrolü
        is_weekend = datetime.now().weekday() >= 5
        
        # Temel trafik yoğunluğu
        base_traffic = np.random.uniform(0.1, 0.3)
        
        # Zirve saat çarpanları
        peak_multiplier = 1.0
        
        if not is_weekend:
            # Sabah zirvesi (07:00-09:30)
            if 7 <= current_hour <= 9 or (current_hour == 9 and current_minute <= 30):
                peak_multiplier = 2.8 if intersection_type == "major" else 2.2
            # Akşam zirvesi (17:00-19:30)
            elif 17 <= current_hour <= 19 or (current_hour == 19 and current_minute <= 30):
                peak_multiplier = 3.2 if intersection_type == "major" else 2.5
            # Öğle arası (12:00-14:00)
            elif 12 <= current_hour <= 14:
                peak_multiplier = 1.8
            # Normal çalışma saatleri
            elif 10 <= current_hour <= 16:
                peak_multiplier = 1.4
        else:
            # Hafta sonu daha düşük trafik
            if 11 <= current_hour <= 15:
                peak_multiplier = 1.6
            elif 19 <= current_hour <= 22:
                peak_multiplier = 1.3
        
        return min(base_traffic * peak_multiplier, 1.0)

    def get_google_maps_traffic_data(self, lat: float, lng: float) -> Dict:
        """Google Maps Distance Matrix API ile trafik süresi verisi çekme"""
        if not self.api_key:
            logger.warning("Google Maps API key bulunamadı, simüle edilmiş veri kullanılacak")
            return None

        try:
            # Test için sabit bir hedef nokta (örnek: Tandoğan Kavşağı)
            destination = "39.9347,32.8197"
            origin = f"{lat},{lng}"

            url = "https://maps.googleapis.com/maps/api/distancematrix/json"
            params = {
                'origins': origin,
                'destinations': destination,
                'departure_time': 'now',
                'key': self.api_key
            }

            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Distance Matrix API hatası: {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"Distance Matrix API çağrısında hata: {e}")
            return None

    def analyze_traffic_data(self) -> List[TrafficData]:
        """Tüm kavşaklar için trafik analizi"""
        traffic_data_list = []

        for intersection in self.intersections:
            maps_data = self.get_google_maps_traffic_data(
                intersection['lat'], 
                intersection['lng']
            )
        
            # Trafik yoğunluğunu hesapla
            density = self.calculate_traffic_density(intersection['type'])
        
            # Varsayılanlar (simüle)
            avg_speed = max(15, int(60 - (density * 45)))  # 15-60 km/h
            wait_time = int(density * 180)
        
            # Eğer gerçek trafik verisi geldiyse, kullan
            if maps_data and maps_data.get("rows"):
                try:
                    element = maps_data["rows"][0]["elements"][0]
                    if "duration_in_traffic" in element:
                        duration_sec = element["duration_in_traffic"]["value"]
                        distance_m = element["distance"]["value"]
                        distance_km = distance_m / 1000
        
                        if duration_sec > 0 and distance_km > 0:
                            avg_speed = int((distance_km / (duration_sec / 3600)))  # km/h
                            wait_time = int(duration_sec % 180)
        
                        logger.info(f"✅ {intersection['name']}: {distance_km:.1f} km, {duration_sec}s, {avg_speed} km/h")
        
                except Exception as e:
                    logger.warning(f"Trafik süresi ayrıştırılamadı: {e}")
        
            # Durum belirleme
            if density > 0.7:
                status = "critical"
            elif density > 0.4:
                status = "moderate"
            else:
                status = "normal"
        
            traffic_data = TrafficData(
                intersection_id=intersection['id'],
                name=intersection['name'],
                lat=intersection['lat'],
                lng=intersection['lng'],
                density=round(density * 100, 1),
                avg_speed=avg_speed,
                wait_time=wait_time,
                vehicle_count=int(density * 150 + np.random.uniform(10, 50)),
                status=status,
                timestamp=datetime.now()
            )
        
            traffic_data_list.append(traffic_data)

        return traffic_data_list

    def save_to_database(self, traffic_data_list: List[TrafficData]):
        """Verileri veritabanına kaydet"""
        conn = sqlite3.connect('traffic_data.db')
        cursor = conn.cursor()
        
        for data in traffic_data_list:
            # AWS mesaj ID'si oluştur
            aws_message_id = str(uuid.uuid4())
            
            cursor.execute('''
                INSERT INTO traffic_logs 
                (intersection_id, name, lat, lng, density, avg_speed, wait_time, vehicle_count, status, timestamp, aws_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data.intersection_id, data.name, data.lat, data.lng,
                data.density, data.avg_speed, data.wait_time, 
                data.vehicle_count, data.status, data.timestamp, aws_message_id
            ))
        
        conn.commit()
        conn.close()
        logger.info(f"{len(traffic_data_list)} veri kaydedildi")

    def send_traffic_data_to_aws(self, traffic_data_list: List[TrafficData]):
        """Trafik verilerini AWS IoT Core'a gönder"""
        try:
            # Ana veri paketi
            aws_payload = {
                'deviceId': self.thing_name,
                'timestamp': datetime.now().isoformat(),
                'location': 'Ankara, Turkey',
                'systemStatus': 'active',
                'dataType': 'traffic_analysis',
                'intersections': []
            }

            # Her kavşak için veri hazırla
            for data in traffic_data_list:
                intersection_data = {
                    'intersectionId': data.intersection_id,
                    'name': data.name,
                    'coordinates': {
                        'latitude': data.lat,
                        'longitude': data.lng
                    },
                    'metrics': {
                        'density': data.density,
                        'averageSpeed': data.avg_speed,
                        'waitTime': data.wait_time,
                        'vehicleCount': data.vehicle_count
                    },
                    'status': data.status,
                    'timestamp': data.timestamp.isoformat(),
                    'alerts': []
                }

                # Kritik durum uyarıları
                if data.status == 'critical':
                    intersection_data['alerts'].append({
                        'type': 'HEAVY_TRAFFIC',
                        'message': f'Yoğun trafik: {data.name}',
                        'severity': 'HIGH'
                    })

                aws_payload['intersections'].append(intersection_data)

            # AWS IoT'ye ana veri gönder
            main_topic = f"ankara-traffic/data/{self.thing_name}"
            self.publish_to_aws_iot(main_topic, aws_payload)

            # Kritik durumlar için özel alert topic
            critical_intersections = [d for d in traffic_data_list if d.status == 'critical']
            if critical_intersections:
                alert_payload = {
                    'alertType': 'TRAFFIC_CONGESTION',
                    'severity': 'HIGH',
                    'timestamp': datetime.now().isoformat(),
                    'affectedIntersections': [
                        {
                            'id': data.intersection_id,
                            'name': data.name,
                            'density': data.density
                        }
                        for data in critical_intersections
                    ],
                    'recommendedAction': 'Consider alternative routes'
                }
                
                alert_topic = f"ankara-traffic/alerts/{self.thing_name}"
                self.publish_to_aws_iot(alert_topic, alert_payload)

            logger.info(f"📤 {len(traffic_data_list)} kavşak verisi AWS IoT'ye gönderildi")

        except Exception as e:
            logger.error(f"AWS IoT veri gönderme hatası: {e}")

    def immediate_analysis(self):
        """AWS'den komut geldiğinde hemen analiz yap"""
        try:
            logger.info("🚀 AWS komutu üzerine hızlı analiz başlatılıyor...")
            traffic_data = self.analyze_traffic_data()
            self.save_to_database(traffic_data)
            self.send_traffic_data_to_aws(traffic_data)
            
            # Node.js API için JSON export
            self.export_to_json(traffic_data)
            
        except Exception as e:
            logger.error(f"Hızlı analiz hatası: {e}")

    def export_to_json(self, traffic_data_list: List[TrafficData]):
        """Node.js API için JSON dosyalarını oluştur"""
        try:
            # Ana trafik verileri
            export_data = {
                'timestamp': datetime.now().isoformat(),
                'source': 'AWS_IoT_Core',
                'intersections': [
                    {
                        'id': data.intersection_id,
                        'name': data.name,
                        'lat': data.lat,
                        'lng': data.lng,
                        'density': data.density,
                        'avgSpeed': data.avg_speed,
                        'waitTime': data.wait_time,
                        'vehicleCount': data.vehicle_count,
                        'status': data.status
                    }
                    for data in traffic_data_list
                ]
            }
            
            with open('current_traffic_data.json', 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)
                
            logger.info("📄 JSON verileri güncellendi")
            
        except Exception as e:
            logger.error(f"JSON export hatası: {e}")

    def get_historical_data(self, hours: int = 24) -> pd.DataFrame:
        """Geçmiş verileri getir"""
        conn = sqlite3.connect('traffic_data.db')
        
        query = '''
            SELECT * FROM traffic_logs 
            WHERE timestamp >= datetime('now', '-{} hours')
            ORDER BY timestamp DESC
        '''.format(hours)
        
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        return df

    def disconnect_aws_iot(self):
        """AWS IoT bağlantısını kapat"""
        try:
            if self.connection:
                disconnect_future = self.connection.disconnect()
                disconnect_future.result()
                logger.info("AWS IoT bağlantısı kapatıldı")
        except Exception as e:
            logger.error(f"AWS IoT bağlantı kapatma hatası: {e}")

def main():
    """Ana uygulama döngüsü"""
    # Çevre değişkenlerini al
    api_key = os.getenv('GOOGLE_MAPS_API_KEY')
    
    analyzer = AWSIoTTrafficAnalyzer(api_key)
    
    # AWS IoT'ye bağlan
    aws_connected = analyzer.connect_to_aws_iot()
    
    if aws_connected:
        logger.info("🚀 AWS IoT Core ile Ankara Trafik Sistemi başlatıldı")
   
    
    try:
        while True:
            # Trafik verilerini analiz et
            traffic_data = analyzer.analyze_traffic_data()
            
            # Veritabanına kaydet
            analyzer.save_to_database(traffic_data)
            
            # AWS IoT'ye gönder (bağlantı varsa)
            if aws_connected:
                analyzer.send_traffic_data_to_aws(traffic_data)
            
            # JSON export (Node.js API için)
            analyzer.export_to_json(traffic_data)
            
            logger.info("🔄 Trafik verileri güncellendi ve AWS'ye gönderildi")
            
            # 30 saniye bekle
            time.sleep(20)
            
    except KeyboardInterrupt:
        logger.info("🛑 Sistem kapatılıyor...")
        analyzer.disconnect_aws_iot()
    except Exception as e:
        logger.error(f"Ana döngüde hata: {e}")
        analyzer.disconnect_aws_iot()

if __name__ == "__main__":
    main()