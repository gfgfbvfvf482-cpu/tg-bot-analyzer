import asyncio
import logging
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, ChatMemberOwner, ChatMemberAdministrator
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv
import re

from ai_analyzer import CommunicationAnalyzer
from message_cache import MessageCache
from config import Config

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ✅ ИСПРАВЛЕНО: инициализация бота перенесена в функцию get_bot()
# Раньше bot и dp создавались на уровне модуля — это вызывало ошибку 500,
# если TELEGRAM_BOT_TOKEN не был задан при импорте модуля на Vercel.
_bot = None
_dp = None
_message_cache = None
_ai_analyzer = None

def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)
    return _bot

def get_dp() -> Dispatcher:
    global _dp
    if _dp is None:
        _dp = Dispatcher()
        _register_handlers(_dp)
    return _dp

def get_message_cache() -> MessageCache:
    global _message_cache
    if _message_cache is None:
        _message_cache = MessageCache(max_size=Config.CACHE_SIZE, memory_cache_size=Config.MEMORY_CACHE_SIZE)
    return _message_cache

def get_ai_analyzer() -> CommunicationAnalyzer:
    global _ai_analyzer
    if _ai_analyzer is None:
        _ai_analyzer = CommunicationAnalyzer()
    return _ai_analyzer

# Track command usage for rate limiting
user_last_command = {}


def is_user_authorized(user_id: int) -> bool:
    return user_id in Config.AUTHORIZED_USERS


def is_main_admin(user_id: int) -> bool:
    return len(Config.AUTHORIZED_USERS) > 0 and user_id == Config.AUTHORIZED_USERS[0]


def add_authorized_user(user_id: int) -> bool:
    if user_id not in Config.AUTHORIZED_USERS:
        Config.AUTHORIZED_USERS.append(user_id)
        return True
    return False


def remove_authorized_user(user_id: int) -> bool:
    if user_id in Config.AUTHORIZED_USERS and not is_main_admin(user_id):
        Config.AUTHORIZED_USERS.remove(user_id)
        return True
    return False


def check_rate_limit(user_id: int) -> bool:
    now = datetime.now()
    if user_id in user_last_command:
        time_diff = now - user_last_command[user_id]
        if time_diff < timedelta(seconds=Config.RATE_LIMIT_SECONDS):
            return False
    user_last_command[user_id] = now
    return True


def escape_markdown_v2(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)


def strip_markdown_formatting(text: str) -> str:
    if not text:
        return text
    text = re.sub(r"\\([_*\[\]()~`>#+\-=|{}.!])", r"\1", text)
    for token in ("**", "__", "`", "*", "_"):
        text = text.replace(token, "")
    return text


async def safe_send_message(bot_or_message, chat_id: int = None, text: str = "", **kwargs):
    """Safely send a message, falling back to plain text if markdown fails"""
    if Config.PLAIN_TEXT_OUTPUT:
        kwargs.pop('parse_mode', None)
        text = strip_markdown_formatting(text)
    try:
        if hasattr(bot_or_message, 'send_message'):
            return await bot_or_message.send_message(chat_id=chat_id, text=text, **kwargs)
        else:
            return await bot_or_message.answer(text=text, **kwargs)
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e).lower():
            kwargs.pop('parse_mode', None)
            text = strip_markdown_formatting(text)
            if hasattr(bot_or_message, 'send_message'):
                return await bot_or_message.send_message(chat_id=chat_id, text=text, **kwargs)
            else:
                return await bot_or_message.answer(text=text, **kwargs)
        else:
            raise


async def safe_edit_message(message, text: str, **kwargs):
    """Safely edit a message"""
    if Config.PLAIN_TEXT_OUTPUT:
        kwargs.pop('parse_mode', None)
        text = strip_markdown_formatting(text)
    try:
        return await message.edit_text(text=text, **kwargs)
    except TelegramBadRequest as e:
        if "message to edit not found" in str(e).lower():
            return None
        elif "can't parse entities" in str(e).lower():
            kwargs.pop('parse_mode', None)
            text = strip_markdown_formatting(text)
            return await message.edit_text(text=text, **kwargs)
        else:
            raise


def _register_handlers(dp: Dispatcher):
    """Регистрируем все обработчики команд"""

    @dp.message(CommandStart())
    async def start_command(message: Message):
        if message.chat.type != ChatType.PRIVATE:
            return
        await safe_send_message(message, text=Config.MESSAGES["welcome_text"], parse_mode='Markdown')

    @dp.message(Command("help"))
    async def help_command(message: Message):
        if message.chat.type != ChatType.PRIVATE:
            return
        help_text = Config.MESSAGES["help_text_template"].format(rate_limit=Config.RATE_LIMIT_SECONDS)
        await safe_send_message(message, text=help_text, parse_mode='Markdown')

    @dp.message(Command("analyze_last_100"))
    async def analyze_last_100(message: Message):
        await handle_analysis_command(message, "last_100")

    @dp.message(Command("analyze_last_24h"))
    async def analyze_last_24h(message: Message):
        await handle_analysis_command(message, "last_24h")

    @dp.message(Command("add_user"))
    async def add_user_command(message: Message):
        if message.chat.type == ChatType.PRIVATE:
            await message.answer(Config.MESSAGES["private_chat_only"])
            return
        if not message.from_user:
            return
        user_id = message.from_user.id
        if not is_main_admin(user_id):
            await message.answer(Config.MESSAGES["main_admin_only_add"])
            return
        if message.reply_to_message and message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
            new_user_id = target_user.id
            username = target_user.username or target_user.first_name or Config.MESSAGES["default_username"]
            if add_authorized_user(new_user_id):
                await message.answer(Config.MESSAGES["user_added"].format(username=username, user_id=new_user_id))
            else:
                await message.answer(Config.MESSAGES["user_already_added"].format(username=username, user_id=new_user_id))
            return
        try:
            command_parts = (message.text or "").split()
            if len(command_parts) != 2:
                await message.answer(Config.MESSAGES["add_user_usage"])
                return
            user_input = command_parts[1]
            if user_input.startswith('@'):
                username = user_input[1:]
                try:
                    chat_member = await get_bot().get_chat_member(message.chat.id, username)
                    new_user_id = chat_member.user.id
                    if add_authorized_user(new_user_id):
                        await message.answer(Config.MESSAGES["user_added"].format(username=username, user_id=new_user_id))
                    else:
                        await message.answer(Config.MESSAGES["user_already_added"].format(username=username, user_id=new_user_id))
                except Exception as e:
                    await message.answer(Config.MESSAGES["user_not_found"].format(username=username))
                    logger.error(f"Error finding user @{username}: {e}")
            else:
                new_user_id = int(user_input)
                if add_authorized_user(new_user_id):
                    await message.answer(Config.MESSAGES["user_added_by_id"].format(user_id=new_user_id))
                else:
                    await message.answer(Config.MESSAGES["user_already_added_by_id"].format(user_id=new_user_id))
        except ValueError:
            await message.answer(Config.MESSAGES["invalid_format"])
        except Exception as e:
            await message.answer(Config.MESSAGES["error_adding_user"].format(error=str(e)))

    @dp.message(Command("remove_user"))
    async def remove_user_command(message: Message):
        if message.chat.type == ChatType.PRIVATE:
            await message.answer(Config.MESSAGES["private_chat_only"])
            return
        if not message.from_user:
            return
        user_id = message.from_user.id
        if not is_main_admin(user_id):
            await message.answer(Config.MESSAGES["main_admin_only_remove"])
            return
        if message.reply_to_message and message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
            target_user_id = target_user.id
            username = target_user.username or target_user.first_name or Config.MESSAGES["default_username"]
            if remove_authorized_user(target_user_id):
                await message.answer(Config.MESSAGES["user_removed"].format(username=username, user_id=target_user_id))
            else:
                await message.answer(Config.MESSAGES["user_cannot_remove"].format(username=username))
            return
        try:
            command_parts = (message.text or "").split()
            if len(command_parts) != 2:
                await message.answer(Config.MESSAGES["remove_user_usage"])
                return
            user_input = command_parts[1]
            if user_input.startswith('@'):
                username = user_input[1:]
                try:
                    chat_member = await get_bot().get_chat_member(message.chat.id, username)
                    target_user_id = chat_member.user.id
                    if remove_authorized_user(target_user_id):
                        await message.answer(Config.MESSAGES["user_removed"].format(username=username, user_id=target_user_id))
                    else:
                        await message.answer(Config.MESSAGES["user_cannot_remove_by_id"].format(username=username, user_id=target_user_id))
                except Exception as e:
                    await message.answer(Config.MESSAGES["user_not_found"].format(username=username))
            else:
                target_user_id = int(user_input)
                if remove_authorized_user(target_user_id):
                    await message.answer(Config.MESSAGES["user_removed_by_id"].format(user_id=target_user_id))
                else:
                    await message.answer(Config.MESSAGES["user_cannot_remove_by_id"].format(username="", user_id=target_user_id))
        except ValueError:
            await message.answer(Config.MESSAGES["invalid_format"])
        except Exception as e:
            await message.answer(Config.MESSAGES["error_removing_user"].format(error=str(e)))

    @dp.message(Command("list_users"))
    async def list_users_command(message: Message):
        if message.chat.type == ChatType.PRIVATE:
            await message.answer(Config.MESSAGES["private_chat_only"])
            return
        if not message.from_user:
            return
        if not is_main_admin(message.from_user.id):
            await message.answer(Config.MESSAGES["main_admin_only_list"])
            return
        if not Config.AUTHORIZED_USERS:
            await message.answer(Config.MESSAGES["user_list_empty"])
            return
        user_list = ""
        for i, uid in enumerate(Config.AUTHORIZED_USERS):
            role = Config.MESSAGES["main_admin_role"] if i == 0 else ""
            user_list += Config.MESSAGES["user_list_item"].format(user_id=uid, role=role)
        await safe_send_message(message, text=Config.MESSAGES["user_list_template"].format(user_list=user_list), parse_mode='Markdown')

    @dp.message(Command("clear_memory"))
    async def clear_memory_command(message: Message):
        if message.chat.type == ChatType.PRIVATE:
            await message.answer(Config.MESSAGES["private_chat_only"])
            return
        if not message.from_user:
            return
        if not is_main_admin(message.from_user.id):
            await message.answer(Config.MESSAGES["main_admin_only_clear"])
            return
        cache = get_message_cache()
        before = cache.get_memory_usage_stats()
        cache.clear_old_messages_from_memory()
        after = cache.get_memory_usage_stats()
        cleared = before['total_messages_in_memory'] - after['total_messages_in_memory']
        stats_text = Config.MESSAGES["memory_cleared_template"].format(
            before_messages=before['total_messages_in_memory'],
            before_chats=before['total_chats_in_memory'],
            after_messages=after['total_messages_in_memory'],
            after_chats=after['total_chats_in_memory'],
            cleared_messages=cleared,
            freed_memory=cleared * 0.5
        )
        await safe_send_message(message, text=stats_text, parse_mode='Markdown')

    @dp.message(Command("chat_stats"))
    async def chat_stats_command(message: Message):
        if message.chat.type == ChatType.PRIVATE:
            await message.answer(Config.MESSAGES["private_chat_only"])
            return
        if not message.from_user:
            return
        if not is_user_authorized(message.from_user.id):
            await message.answer(Config.MESSAGES["not_authorized"])
            return
        cache = get_message_cache()
        chat_id = message.chat.id
        cache_stats = cache.get_chat_stats(chat_id)
        memory_stats = cache.get_memory_usage_stats()
        oldest_message = cache_stats['oldest_message'].strftime('%Y-%m-%d %H:%M') if cache_stats['oldest_message'] else Config.MESSAGES["no_messages"]
        newest_message = cache_stats['newest_message'].strftime('%Y-%m-%d %H:%M') if cache_stats['newest_message'] else Config.MESSAGES["no_messages"]
        if cache_stats['total_messages'] == 0:
            warning_message = Config.MESSAGES["empty_cache_warning"]
        elif cache_stats['total_messages'] < 10:
            warning_message = Config.MESSAGES["low_messages_warning"]
        else:
            warning_message = ""
        stats_text = Config.MESSAGES["chat_stats_template"].format(
            chat_title=message.chat.title,
            total_messages=cache_stats['total_messages'],
            memory_messages=len(cache.chats.get(chat_id, [])),
            unique_users=cache_stats['unique_users'],
            oldest_message=oldest_message,
            newest_message=newest_message,
            cache_size=Config.CACHE_SIZE,
            memory_cache_size=Config.MEMORY_CACHE_SIZE,
            total_memory_messages=memory_stats['total_messages_in_memory'],
            total_chats_in_memory=memory_stats['total_chats_in_memory'],
            warning_message=warning_message
        )
        await safe_send_message(message, text=stats_text, parse_mode='Markdown')

    @dp.message(Command("my_communication"))
    async def my_communication_command(message: Message):
        if message.chat.type == ChatType.PRIVATE:
            await message.answer(Config.MESSAGES["private_chat_only"])
            return
        if not message.from_user:
            return
        user_id = message.from_user.id
        chat_id = message.chat.id
        username = message.from_user.username or message.from_user.first_name or "Пользователь"
        if not is_user_authorized(user_id):
            await message.answer(Config.MESSAGES["not_authorized"])
            return
        if not check_rate_limit(user_id):
            await message.answer(Config.MESSAGES["rate_limit"].format(rate_limit=Config.RATE_LIMIT_SECONDS))
            return
        thinking_msg = await message.answer(Config.MESSAGES["analyzing_communication"])
        try:
            cache = get_message_cache()
            user_messages = cache.get_user_messages(chat_id, user_id)
            interactions = cache.get_user_interactions(chat_id, user_id)
            if not user_messages:
                await safe_edit_message(thinking_msg, Config.MESSAGES["no_messages_for_analysis"])
                return
            analysis_result = await get_ai_analyzer().analyze_user_communication(user_messages, interactions, username)
            await thinking_msg.delete()
            await safe_send_message(get_bot(), chat_id=user_id, text=analysis_result, parse_mode='Markdown')
            await message.answer(Config.MESSAGES["analysis_sent_private"].format(username=username))
        except Exception as e:
            await safe_edit_message(thinking_msg, Config.MESSAGES["analysis_error"].format(error=str(e)))
            logger.error(f"Personal analysis error: {e}")

    @dp.message(Command("analyze_user"))
    async def analyze_user_command(message: Message):
        if message.chat.type == ChatType.PRIVATE:
            await message.answer(Config.MESSAGES["private_chat_only"])
            return
        if not message.from_user:
            return
        user_id = message.from_user.id
        chat_id = message.chat.id
        if not is_user_authorized(user_id):
            await message.answer(Config.MESSAGES["not_authorized"])
            return
        if not check_rate_limit(user_id):
            await message.answer(Config.MESSAGES["rate_limit"].format(rate_limit=Config.RATE_LIMIT_SECONDS))
            return
        target_user_id = None
        target_username = None
        if message.reply_to_message and message.reply_to_message.from_user:
            target_user_id = message.reply_to_message.from_user.id
            target_username = message.reply_to_message.from_user.username or message.reply_to_message.from_user.first_name or Config.MESSAGES["default_username"]
        else:
            command_parts = (message.text or "").split()
            if len(command_parts) < 2:
                await message.answer(Config.MESSAGES["analyze_user_usage"])
                return
            user_input = command_parts[1]
            if user_input.startswith('@'):
                username = user_input[1:]
                cache = get_message_cache()
                all_messages = cache.get_last_n_messages(chat_id, 1000)
                for msg in all_messages:
                    if msg.get('username', '').lower() == username.lower():
                        target_user_id = msg['user_id']
                        target_username = msg['username']
                        break
                if not target_user_id:
                    await message.answer(f"❌ Пользователь @{username} не найден в кеше сообщений.")
                    return
            else:
                await message.answer("❌ Неверный формат. Используйте @username.")
                return
        if not target_user_id:
            await message.answer("❌ Не удалось определить пользователя для анализа.")
            return
        thinking_msg = await message.answer(f"🤔 Анализирую стиль коммуникации {target_username}...")
        try:
            cache = get_message_cache()
            user_messages = cache.get_user_messages(chat_id, target_user_id)
            interactions = cache.get_user_interactions(chat_id, target_user_id)
            if not user_messages:
                await safe_edit_message(thinking_msg, f"❌ Нет сообщений от {target_username}.")
                return
            analysis_result = await get_ai_analyzer().analyze_user_communication(user_messages, interactions, target_username)
            await thinking_msg.delete()
            await safe_send_message(get_bot(), chat_id=user_id, text=analysis_result, parse_mode='Markdown')
            await message.answer(f"✅ Анализ {target_username} отправлен в личные сообщения.")
        except Exception as e:
            await safe_edit_message(thinking_msg, f"❌ Ошибка при анализе: {str(e)}")
            logger.error(f"User analysis error: {e}")

    @dp.message(Command("analyze_user_all"))
    async def analyze_user_all_command(message: Message):
        if not message.from_user:
            return
        user_id = message.from_user.id
        is_private_chat = message.chat.type == ChatType.PRIVATE
        if not is_user_authorized(user_id):
            await message.answer("❌ У вас нет прав для использования этой команды.")
            return
        if not check_rate_limit(user_id):
            await message.answer(f"⏱️ Подождите {Config.RATE_LIMIT_SECONDS} секунд.")
            return
        target_user_id = None
        target_username = None
        if not is_private_chat and message.reply_to_message and message.reply_to_message.from_user:
            target_user_id = message.reply_to_message.from_user.id
            target_username = message.reply_to_message.from_user.username or message.reply_to_message.from_user.first_name or "Пользователь"
        else:
            command_parts = (message.text or "").split()
            if len(command_parts) < 2:
                await message.answer("❌ Использование: /analyze_user_all @username или <user_id>")
                return
            user_input = command_parts[1]
            cache = get_message_cache()
            if user_input.startswith('@'):
                username = user_input[1:]
                for cid in cache.get_all_chats():
                    all_messages = cache.get_last_n_messages(cid, 1000)
                    for msg in all_messages:
                        if msg.get('username', '').lower() == username.lower():
                            target_user_id = msg['user_id']
                            target_username = msg['username']
                            break
                    if target_user_id:
                        break
                if not target_user_id:
                    await message.answer(f"❌ Пользователь @{username} не найден.")
                    return
            elif user_input.isdigit():
                target_user_id = int(user_input)
                for cid in cache.get_all_chats():
                    for msg in cache.get_last_n_messages(cid, 1000):
                        if msg.get('user_id') == target_user_id:
                            target_username = msg.get('username', f"User_{target_user_id}")
                            break
                    if target_username:
                        break
                if not target_username:
                    target_username = f"User_{target_user_id}"
            else:
                await message.answer("❌ Неверный формат.")
                return
        thinking_msg = await message.answer("🤔 Анализирую стиль коммуникации из всех чатов...")
        try:
            cache = get_message_cache()
            user_messages = cache.get_user_messages_all_chats(target_user_id)
            interactions = cache.get_user_interactions_all_chats(target_user_id)
            user_stats = cache.get_user_chat_stats(target_user_id)
            if not user_messages:
                await thinking_msg.edit_text(f"❌ Нет сообщений от {target_username} ни в одном чате.")
                return
            analysis_result = await get_ai_analyzer().analyze_user_communication(user_messages, interactions, target_username)
            stats_summary = (
                f"\n\n📊 *Статистика по всем чатам:*\n"
                f"• Всего сообщений: {user_stats['total_messages']}\n"
                f"• Чатов с активностью: {user_stats['chats_count']}\n"
            )
            if user_stats['oldest_message'] and user_stats['newest_message']:
                stats_summary += (
                    f"• Период: {user_stats['oldest_message'].strftime('%Y-%m-%d')} - "
                    f"{user_stats['newest_message'].strftime('%Y-%m-%d')}\n"
                )
            full_analysis = analysis_result + stats_summary
            await thinking_msg.delete()
            if is_private_chat:
                await safe_send_message(message, text=full_analysis, parse_mode='Markdown')
            else:
                await safe_send_message(get_bot(), chat_id=user_id, text=full_analysis, parse_mode='Markdown')
                await message.answer(f"✅ Анализ {target_username} из всех чатов отправлен в личные сообщения.")
        except Exception as e:
            await safe_edit_message(thinking_msg, f"❌ Ошибка: {str(e)}")
            logger.error(f"Cross-chat analysis error: {e}")

    @dp.message(Command("conflict"))
    async def cmd_conflict(message: Message):
        if message.chat.type == ChatType.PRIVATE:
            await message.answer("❌ Эта команда работает только в групповых чатах.")
            return
        if not message.from_user:
            return
        user_id = message.from_user.id
        chat_id = message.chat.id
        if not check_rate_limit(user_id):
            await message.answer(Config.MESSAGES["rate_limit"].format(rate_limit=Config.RATE_LIMIT_SECONDS))
            return
        messages = get_message_cache().get_last_n_messages(chat_id, 100)
        if not messages:
            await message.answer("❌ Недостаточно сообщений.")
            return
        thinking_msg = await message.answer("🔍 Анализирую конфликт... ⏳")
        try:
            analysis = await get_ai_analyzer().analyze_conflict(messages)
            await thinking_msg.delete()
            await safe_send_message(message, text=f"📝 *Анализ конфликта:*\n\n{analysis}", parse_mode='Markdown')
        except Exception as e:
            await safe_edit_message(thinking_msg, f"❌ Ошибка: {str(e)}")
            logger.error(f"Conflict analysis error: {e}")

    @dp.message(Command("digest"))
    async def cmd_digest(message: Message):
        if message.chat.type == ChatType.PRIVATE:
            await message.answer("❌ Эта команда работает только в групповых чатах.")
            return
        if not message.from_user:
            return
        user_id = message.from_user.id
        chat_id = message.chat.id
        if not check_rate_limit(user_id):
            await message.answer(Config.MESSAGES["rate_limit"].format(rate_limit=Config.RATE_LIMIT_SECONDS))
            return
        since = datetime.now() - timedelta(hours=24)
        messages = get_message_cache().get_messages_since(chat_id, since)
        if not messages:
            await message.answer("❌ За последние 24 часа не было сообщений.")
            return
        thinking_msg = await message.answer("🔍 Собираю советы за 24 часа... ⏳")
        try:
            tips = await get_ai_analyzer().analyze_tips(messages)
            await thinking_msg.delete()
            await safe_send_message(message, text=f"💡 *Дайджест советов:*\n\n{tips}", parse_mode='Markdown')
        except Exception as e:
            await safe_edit_message(thinking_msg, f"❌ Ошибка: {str(e)}")
            logger.error(f"Tips analysis error: {e}")

    @dp.message(F.text & F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    async def cache_group_message(message: Message):
        if message.text and message.text.startswith('/'):
            return
        if not message.from_user or not message.text:
            return
        get_message_cache().add_message(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            username=message.from_user.username or message.from_user.first_name or "Пользователь",
            text=message.text,
            timestamp=datetime.now()
        )


async def handle_analysis_command(message: Message, analysis_type: str):
    """Общая логика для команд анализа"""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Команды анализа работают только в групповых чатах.")
        return
    if not message.from_user:
        return
    user_id = message.from_user.id
    chat_id = message.chat.id
    if not is_user_authorized(user_id):
        await message.answer("❌ У вас нет прав для использования этой команды.")
        return
    if not check_rate_limit(user_id):
        await message.answer(f"⏱ Подождите {Config.RATE_LIMIT_SECONDS} секунд.")
        return
    cache = get_message_cache()
    if analysis_type == "last_100":
        messages = cache.get_last_n_messages(chat_id, 100)
        analysis_description = "последних 100 сообщений"
    elif analysis_type == "last_24h":
        messages = cache.get_messages_since(chat_id, datetime.now() - timedelta(hours=24))
        analysis_description = "сообщений за последние 24 часа"
    else:
        await message.answer("❌ Неизвестный тип анализа.")
        return
    if not messages:
        cache_stats = cache.get_chat_stats(chat_id)
        if cache_stats['total_messages'] == 0:
            await message.answer(
                "❌ Нет сообщений для анализа.\n\n"
                "Бот сохраняет сообщения только после добавления в чат. "
                "Подождите, пока участники напишут несколько сообщений."
            )
        else:
            await message.answer(
                f"❌ Недостаточно сообщений для анализа {analysis_description}.\n"
                f"Всего в кеше: {cache_stats['total_messages']} сообщений."
            )
        return
    await message.answer(
        f"🔍 Анализирую {analysis_description} ({len(messages)} сообщений). "
        f"Результат будет отправлен в личные сообщения."
    )
    try:
        await get_bot().send_message(
            user_id,
            f"🔄 Анализирую {analysis_description} из чата «{message.chat.title}». Это займёт несколько секунд..."
        )
    except Exception as e:
        logger.error(f"Failed to send private notification: {e}")
        await message.answer("❌ Не удалось отправить сообщение в личку. Сначала напишите боту /start в личных сообщениях.")
        return
    try:
        analysis_result = await get_ai_analyzer().analyze_messages(messages)
        await safe_send_message(
            get_bot(),
            chat_id=user_id,
            text=f"📊 *Анализ коммуникаций: {message.chat.title}*\n\n{analysis_result}",
            parse_mode='Markdown'
        )
        logger.info(f"Analysis completed for user {user_id} in chat {chat_id}")
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        await safe_send_message(
            get_bot(),
            chat_id=user_id,
            text="❌ Ошибка при анализе. Попробуйте позже."
        )


# ✅ ИСПРАВЛЕНО: функция handle_update теперь использует get_bot() и get_dp()
async def handle_update(update_data: dict):
    """Обработка webhook-обновления от Telegram"""
    from aiogram.types import Update
    update = Update(**update_data)
    await get_dp().feed_update(get_bot(), update)


async def main():
    """Запуск бота в режиме polling (только для локальной разработки)"""
    logger.info("Запуск бота в режиме polling (локальная разработка)...")
    
    if not Config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан!")
        return
    if not Config.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY не задан!")
        return
    if not Config.AUTHORIZED_USERS:
        logger.warning("AUTHORIZED_USERS не заданы. Установите в .env файле.")
    
    bot = get_bot()
    dp = get_dp()
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    # Запускаем polling только при локальном запуске
    # На Vercel этот блок не выполняется — используется webhook через api/webhook.py
    asyncio.run(main())
