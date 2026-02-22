from flask import Flask, request

app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    return 'Bot is running!'

@app.route('/webhook', methods=['POST', 'GET'])
def webhook():
    return 'OK'