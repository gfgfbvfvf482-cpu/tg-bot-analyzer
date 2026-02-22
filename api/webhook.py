import sys
import os
import json
import logging
from flask import Flask, request, jsonify

# Добавляем путь к корневой папке проекта
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import bot, dp, handle_update

app = Flask(__name__)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработка вебхуков от Telegram"""
    try:
        update_data = request.get_json()
        logger.info(f"Received update: {update_data.get('update_id')}")
        
        # Обрабатываем обновление асинхронно
        # В Flask нельзя использовать asyncio.run(), поэтому используем другой подход
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(handle_update(update_data))
        loop.close()
        
        return 'OK', 200
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        return 'Error', 500

@app.route('/', methods=['GET'])
def index():
    return 'Bot is running!', 200 