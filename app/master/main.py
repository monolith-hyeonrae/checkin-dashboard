from flask import Flask, render_template
import os

app = Flask(__name__)

# 추후 설정파일로 이동 가능
DEVICE_IPS = [
    "10.221.232.31",
    "10.221.232.32",
    "10.221.232.33",
    "10.221.232.34",
    "10.221.232.35",
    "10.221.232.36",
    "10.221.232.37",
    "10.221.232.38",
    "10.221.232.39",
    "10.221.232.40"
]

@app.route('/')
def index():
    return render_template('dashboard.html', devices=DEVICE_IPS)

if __name__ == '__main__':
    app.run(host='10.221.232.194', port=9811, debug=True)
