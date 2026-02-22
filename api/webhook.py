import sys
import os
import asyncio
import logging
from flask import Flask, request, jsonify

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import bot, dp, handle_update

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update_data = request.get_json(force=True, silent=True)
        if not update_data:
            return 'Bad Request', 400

        logger.info(f"Received update: {update_data.get('update_id')}")

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(handle_update(update_data))
        finally:
            loop.close()

        return 'OK', 200
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        return 'Error', 500

@app.route('/', methods=['GET'])
def index():
    return 'Bot is running!', 200 