require('dotenv').config();
const express = require('express');
const cors = require('cors');
const fs = require('fs').promises;
const mqtt = require('mqtt');
const WebSocket = require('ws');
const sqlite3 = require('sqlite3').verbose();
const { promisify } = require('util');

const app = express();
const PORT = process.env.PORT || 3001;
const WS_PORT = process.env.WS_PORT || 8080;

// Middleware
app.use(cors());
app.use(express.json());
app.use(express.static('public'));

// Veritabanı ve MQTT
const db = new sqlite3.Database('traffic_data.db');
const dbAll = promisify(db.all.bind(db));
const mqttClient = mqtt.connect('mqtt://localhost:1883');
const wss = new WebSocket.Server({ port: WS_PORT });

let latestTrafficData = null;

// Utility fonksiyonlar
const readJsonFile = async (filename) => {
    try {
        const data = await fs.readFile(filename, 'utf8');
        return JSON.parse(data);
    } catch (error) {
        return null;
    }
};

// MQTT handlers
mqttClient.on('connect', () => {
    console.log('MQTT bağlandı');
    mqttClient.subscribe('traffic/sensors/+/data');
    mqttClient.subscribe('traffic/analysis/update');
});

mqttClient.on('message', (topic, message) => {
    const data = JSON.parse(message.toString());
    if (topic === 'traffic/analysis/update') {
        latestTrafficData = data;
        broadcastToWebSocket(data);
    }
});

// WebSocket broadcast
const broadcastToWebSocket = (data) => {
    wss.clients.forEach(client => {
        if (client.readyState === WebSocket.OPEN) {
            client.send(JSON.stringify({
                type: 'traffic_update',
                data: data,
                timestamp: new Date().toISOString()
            }));
        }
    });
};

// API Routes
app.get('/api/traffic/current', async (req, res) => {
    const data = await readJsonFile('current_traffic_data.json');
    res.json({ success: !!data, data: data || 'Veri bulunamadı' });
});

app.get('/api/traffic/intersection/:id', async (req, res) => {
    const data = await readJsonFile('current_traffic_data.json');
    if (data?.intersections) {
        const intersection = data.intersections.find(i => i.id === parseInt(req.params.id));
        res.json({ success: !!intersection, data: intersection || 'Kavşak bulunamadı' });
    } else {
        res.json({ success: false, data: 'Veri bulunamadı' });
    }
});

app.get('/api/traffic/predictions', async (req, res) => {
    const data = await readJsonFile('traffic_predictions.json');
    res.json({ success: !!data, data: data || 'Tahmin bulunamadı' });
});

app.get('/api/traffic/history', async (req, res) => {
    const hours = parseInt(req.query.hours) || 24;
    const intersectionId = req.query.intersection_id;
    
    let query = `SELECT * FROM traffic_logs WHERE timestamp >= datetime('now', '-${hours} hours')`;
    const params = [];
    
    if (intersectionId) {
        query += ' AND intersection_id = ?';
        params.push(intersectionId);
    }
    query += ' ORDER BY timestamp DESC LIMIT 1000';
    
    try {
        const rows = await dbAll(query, params);
        res.json({ success: true, data: rows, count: rows.length });
    } catch (error) {
        res.json({ success: false, data: 'Veritabanı hatası' });
    }
});

app.get('/api/traffic/stats', async (req, res) => {
    const data = await readJsonFile('current_traffic_data.json');
    
    if (data?.intersections) {
        const intersections = data.intersections;
        const stats = {
            totalIntersections: intersections.length,
            totalVehicles: intersections.reduce((sum, i) => sum + i.vehicleCount, 0),
            averageSpeed: Math.round(intersections.reduce((sum, i) => sum + i.avgSpeed, 0) / intersections.length),
            averageWaitTime: Math.round(intersections.reduce((sum, i) => sum + i.waitTime, 0) / intersections.length),
            criticalIntersections: intersections.filter(i => i.status === 'critical').length,
            moderateIntersections: intersections.filter(i => i.status === 'moderate').length,
            normalIntersections: intersections.filter(i => i.status === 'normal').length
        };
        res.json({ success: true, data: stats });
    } else {
        res.json({ success: false, data: 'İstatistik hesaplanamadı' });
    }
});

app.post('/api/traffic/route/optimize', async (req, res) => {
    const { origin, destination, avoidCritical = true } = req.body;
    
    if (!origin || !destination) {
        return res.json({ success: false, message: 'Başlangıç ve hedef gerekli' });
    }
    
    const data = await readJsonFile('current_traffic_data.json');
    
    if (data?.intersections) {
        const criticalIntersections = data.intersections
            .filter(i => i.status === 'critical')
            .map(i => ({ lat: i.lat, lng: i.lng, name: i.name }));
        
        const recommendation = {
            route: {
                origin,
                destination,
                avoidPoints: avoidCritical ? criticalIntersections : [],
                estimatedDuration: calculateDuration(data.intersections)
            },
            trafficConditions: {
                overall: getTrafficCondition(data.intersections),
                criticalAreas: criticalIntersections.length
            }
        };
        
        res.json({ success: true, data: recommendation });
    } else {
        res.json({ success: false, data: 'Rota hesaplanamadı' });
    }
});

app.post('/api/iot/sensor/data', async (req, res) => {
    const { sensorId, intersectionId, data } = req.body;
    
    if (!sensorId || !intersectionId || !data) {
        return res.json({ success: false, message: 'Eksik veri' });
    }
    
    const message = {
        sensorId,
        intersectionId,
        data,
        timestamp: new Date().toISOString()
    };
    
    mqttClient.publish(`traffic/sensors/${sensorId}/data`, JSON.stringify(message));
    res.json({ success: true, message: 'Veri alındı' });
});

// Yardımcı fonksiyonlar
const calculateDuration = (intersections) => {
    const avgSpeed = intersections.reduce((sum, i) => sum + i.avgSpeed, 0) / intersections.length;
    const avgWaitTime = intersections.reduce((sum, i) => sum + i.waitTime, 0) / intersections.length;
    return Math.round((10 / avgSpeed) * 60 + (avgWaitTime / 60));
};

const getTrafficCondition = (intersections) => {
    const criticalRatio = intersections.filter(i => i.status === 'critical').length / intersections.length;
    const moderateRatio = intersections.filter(i => i.status === 'moderate').length / intersections.length;
    
    if (criticalRatio > 0.4) return 'heavy';
    if (criticalRatio > 0.2 || moderateRatio > 0.5) return 'moderate';
    return 'light';
};

// WebSocket bağlantıları
wss.on('connection', (ws) => {
    console.log('WebSocket bağlandı');
    
    if (latestTrafficData) {
        ws.send(JSON.stringify({
            type: 'initial_data',
            data: latestTrafficData,
            timestamp: new Date().toISOString()
        }));
    }
});

// Periyodik güncelleme
setInterval(async () => {
    const data = await readJsonFile('current_traffic_data.json');
    if (data && JSON.stringify(data) !== JSON.stringify(latestTrafficData)) {
        latestTrafficData = data;
        broadcastToWebSocket(data);
    }
}, 30000);

// 404 handler
app.use('*', (req, res) => {
    res.status(404).json({
        success: false,
        message: 'Endpoint bulunamadı',
        endpoints: [
            'GET /api/traffic/current',
            'GET /api/traffic/intersection/:id',
            'GET /api/traffic/predictions',
            'GET /api/traffic/history',
            'GET /api/traffic/stats',
            'POST /api/traffic/route/optimize',
            'POST /api/iot/sensor/data'
        ]
    });
});

// Sunucu başlat
app.listen(PORT, () => {
    console.log(`API: http://localhost:${PORT}`);
    console.log(`MQTT: mqtt://localhost:1883`);
});

// Graceful shutdown
process.on('SIGTERM', () => {
    console.log('Sunucu kapatılıyor...');
    mqttClient.end();
    db.close();
    process.exit(0);
});

