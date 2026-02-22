from flask import Flask, request
import logging

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    """Главная страница для проверки"""
    logger.info("GET request to root")
    return 'Bot is running!', 200

@app.route('/webhook', methods=['POST', 'GET'])
def webhook():
    """Простой вебхук без всяких проверок"""
    try:
        # Логируем любой запрос
        logger.info(f"Received {request.method} request to /webhook")
        logger.info(f"Headers: {dict(request.headers)}")
        
        if request.method == 'POST':
            data = request.get_json()
            logger.info(f"POST data: {data}")
        
        # Всегда отвечаем OK
        return 'OK', 200
    except Exception as e:
        logger.error(f"Error: {e}")
        return 'OK', 200  # Даже при ошибке отвечаем OK 