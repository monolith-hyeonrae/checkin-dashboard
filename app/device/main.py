from flask import Flask, render_template, request, redirect, session, Response, url_for, jsonify, send_file
from flask_cors import CORS
import yaml
import cv2
import time
import datetime
import os
import subprocess
import json
import threading
import ast
from threading import Lock

app = Flask(__name__)
CORS(app)
app.secret_key = 'your_secret_key_here'
CONFIG_PATH = os.path.expanduser('~/OCR/config.yaml')
VIDEO_DEVICE = '/dev/video11'
latest_thumbnail_path = '/tmp/ocr_thumbnail.jpg'

# ---------------------------- Camera Manager ---------------------------- #
class CameraManager:
    def __init__(self):
        self.cap = None
        self.lock = Lock()
        self.feed_active = False

    def open(self):
        if self.cap is None:
            self.cap = cv2.VideoCapture(VIDEO_DEVICE)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 848)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    def close(self):
        if self.cap:
            self.cap.release()
            self.cap = None

    def get_frame(self):
        with self.lock:
            self.open()
            success, frame = self.cap.read()
            return frame if success else None

camera_manager = CameraManager()

# ---------------------------- Utility ---------------------------- #
def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)

def save_config(config):
    with open(CONFIG_PATH, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

def rotate_image(img, angle):
    (h, w) = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(img, M, (w, h))

# ---------------------------- Thumbnail Updater ---------------------------- #
def run_thumbnail_updater():
    while True:
        if not camera_manager.feed_active:
            frame = camera_manager.get_frame()
            if frame is not None:
                config = load_config()
                angle = config['PREPROC'].get('SOURCE_DEGREE', 0.0)
                rotated = rotate_image(frame, angle)
                thumbnail = cv2.resize(rotated, (424, 240))
                cv2.imwrite(latest_thumbnail_path, thumbnail)
        time.sleep(10)

threading.Thread(target=run_thumbnail_updater, daemon=True).start()

# ---------------------------- Flask Routes ---------------------------- #
@app.route('/')
def index():
    config = load_config()
    return render_template(
        'dashboard.html',
        config=config,
        device_status=os.path.exists('/dev/video0'),
        vstream_status=check_service_status('981park-cam-vstream.service'),
        ocr_status=check_service_status('981park-ocr-checkin.service'),
        send_logs=get_send_logs('981park-ocr-checkin.service')
    )

@app.route('/video_feed')
def video_feed():
    def generate():
        camera_manager.feed_active = True
        try:
            while True:
                frame = camera_manager.get_frame()
                if frame is None:
                    continue
                config = load_config()
                angle = config['PREPROC'].get('SOURCE_DEGREE', 0.0)
                roi = config['THRESHOLD']['ROI']
                x1, y1 = roi[0]
                x2, y2 = roi[1]
                rotated = rotate_image(frame, angle)

                for x in range(0, 849, 50):
                    cv2.line(rotated, (x, 0), (x, 480), (180,180,180), 1)
                    cv2.putText(rotated, str(x), (x+2, 12), cv2.FONT_HERSHEY_PLAIN, 0.9, (255,255,255), 1)
                for y in range(0, 481, 40):
                    cv2.line(rotated, (0, y), (848, y), (180,180,180), 1)
                    cv2.putText(rotated, str(y), (2, y+12), cv2.FONT_HERSHEY_PLAIN, 0.9, (255,255,255), 1)

                cv2.rectangle(rotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(rotated, f"Rotated: {angle:.1f} deg", (10, 470), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,0), 2)

                preview = cv2.resize(frame, (212, 120))
                rotated[10:130, 626:838] = preview
                _, buffer = cv2.imencode('.jpg', rotated)
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                time.sleep(0.2)
        finally:
            camera_manager.feed_active = False
            camera_manager.close()

    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/thumbnail.jpg')
def thumbnail():
    if os.path.exists(latest_thumbnail_path):
        return send_file(latest_thumbnail_path, mimetype='image/jpeg')
    else:
        return '', 404

@app.route('/update', methods=['POST'])
def update():
    config = load_config()
    config['PREPROC']['SOURCE_DEGREE'] = float(request.form['source_degree'])
    config['THRESHOLD']['CONFIDENCE'] = float(request.form['confidence'])
    config['THRESHOLD']['ROI'] = ast.literal_eval(request.form['roi'])
    config['THRESHOLD']['WIDTH'] = int(request.form['thresh_width'])
    config['THRESHOLD']['HEIGHT'] = int(request.form['thresh_height'])
    save_config(config)
    return redirect(url_for('index'))

@app.route('/api/status')
def api_status():
    thumbnail_mtime = None
    if os.path.exists(latest_thumbnail_path):
        ts = os.path.getmtime(latest_thumbnail_path)
        thumbnail_mtime = datetime.datetime.fromtimestamp(ts).isoformat()
    return jsonify({
        "ip": request.host.split(":")[0],
        "deviceDt": datetime.datetime.now().isoformat(),
        "thumbnailUrl": "/thumbnail.jpg",
        "thumbnailUpdated": thumbnail_mtime,
        "vstream_status": check_service_status('981park-cam-vstream.service'),
        "ocr_status": check_service_status('981park-ocr-checkin.service'),
        "online": True
    })

@app.route('/api/send_logs')
def api_send_logs():
    return jsonify(get_send_logs('981park-ocr-checkin.service'))

# ---------------------------- System Utils ---------------------------- #
def check_service_status(service_name):
    result = subprocess.run(['systemctl', 'is-active', service_name], stdout=subprocess.PIPE)
    return result.stdout.decode().strip()

def get_send_logs(service_name, limit=3):
    try:
        result = subprocess.run(
            ['journalctl', '-u', service_name, '--no-pager', '--output=short-iso'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        logs = result.stdout.decode().split('\n')
        send_lines = [line for line in logs if 'SEND {' in line][-limit:]

        extracted = []
        for line in send_lines:
            json_part = line.split('SEND ', 1)[-1].strip()
            try:
                data = json.loads(json_part)
                extracted.append({
                    'deviceDt': data.get('deviceDt', 'N/A'),
                    'carNumber': data.get('carNumber', 'N/A'),
                    'trackId': data.get('trackId', 'N/A')
                })
            except json.JSONDecodeError:
                continue
        return extracted[::-1]
    except Exception:
        return []

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9810, debug=False)
