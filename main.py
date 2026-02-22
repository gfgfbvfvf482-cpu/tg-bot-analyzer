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

# Initialize bot and dispatcher
bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Initialize services
message_cache = MessageCache(max_size=Config.CACHE_SIZE, memory_cache_size=Config.MEMORY_CACHE_SIZE)
ai_analyzer = CommunicationAnalyzer()

# Track command usage for rate limiting
user_last_command = {}


def is_user_authorized(user_id: int) -> bool:
    """Check if user is in the authorized users list"""
    return user_id in Config.AUTHORIZED_USERS


def is_main_admin(user_id: int) -> bool:
    """Check if user is the main admin (first in the list)"""
    return len(Config.AUTHORIZED_USERS) > 0 and user_id == Config.AUTHORIZED_USERS[0]


def add_authorized_user(user_id: int) -> bool:
    """Add user to authorized list"""
    if user_id not in Config.AUTHORIZED_USERS:
        Config.AUTHORIZED_USERS.append(user_id)
        return True
    return False


def remove_authorized_user(user_id: int) -> bool:
    """Remove user from authorized list"""
    if user_id in Config.AUTHORIZED_USERS and not is_main_admin(user_id):
        Config.AUTHORIZED_USERS.remove(user_id)
        return True
    return False


def check_rate_limit(user_id: int) -> bool:
    """Check if user can execute command (rate limiting)"""
    now = datetime.now()
    if user_id in user_last_command:
        time_diff = now - user_last_command[user_id]
        if time_diff < timedelta(seconds=Config.RATE_LIMIT_SECONDS):
            return False
    user_last_command[user_id] = now
    return True


def escape_markdown_v2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2"""
    # Characters that need to be escaped in MarkdownV2
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)


def strip_markdown_formatting(text: str) -> str:
    """Remove common Telegram Markdown/MarkdownV2 formatting to produce plain text."""
    if not text:
        return text
    # Unescape MarkdownV2 escapes
    text = re.sub(r"\\([_*\[\]()~`>#+\-=|{}.!])", r"\1", text)
    # Remove bold/italic/underline/strikethrough/code markers
    for token in ("**", "__", "`", "*", "_"):
        text = text.replace(token, "")
    return text


async def safe_send_message(bot_or_message, chat_id: int = None, text: str = "", **kwargs):
    """Safely send a message, falling back to plain text if markdown fails"""
    if Config.PLAIN_TEXT_OUTPUT:
        kwargs.pop('parse_mode', None)
        text = strip_markdown_formatting(text)
    try:
        if hasattr(bot_or_message, 'send_message'):  # It's a bot instance
            return await bot_or_message.send_message(chat_id=chat_id, text=text, **kwargs)
        else:  # It's a message instance
            return await bot_or_message.answer(text=text, **kwargs)
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e).lower():
            # Remove parse_mode and try again with plain text
            kwargs.pop('parse_mode', None)
            text = strip_markdown_formatting(text)
            if hasattr(bot_or_message, 'send_message'):
                return await bot_or_message.send_message(chat_id=chat_id, text=text, **kwargs)
            else:
                return await bot_or_message.answer(text=text, **kwargs)
        else:
            raise


async def safe_edit_message(message, text: str, **kwargs):
    """Safely edit a message, handling cases where message might not exist"""
    if Config.PLAIN_TEXT_OUTPUT:
        kwargs.pop('parse_mode', None)
        text = strip_markdown_formatting(text)
    try:
        return await message.edit_text(text=text, **kwargs)
    except TelegramBadRequest as e:
        if "message to edit not found" in str(e).lower():
            # Message was already deleted, do nothing
            return None
        elif "can't parse entities" in str(e).lower():
            # Remove parse_mode and try again with plain text
            kwargs.pop('parse_mode', None)
            text = strip_markdown_formatting(text)
            return await message.edit_text(text=text, **kwargs)
        else:
            raise


@dp.message(CommandStart())
async def start_command(message: Message):
    """Handle /start command in private messages"""
    if message.chat.type != ChatType.PRIVATE:
        return
    
    await safe_send_message(message, text=Config.MESSAGES["welcome_text"], parse_mode='Markdown')


@dp.message(Command("help"))
async def help_command(message: Message):
    """Handle /help command in private messages"""
    if message.chat.type != ChatType.PRIVATE:
        return
    
    help_text = Config.MESSAGES["help_text_template"].format(rate_limit=Config.RATE_LIMIT_SECONDS)
    await safe_send_message(message, text=help_text, parse_mode='Markdown')


@dp.message(Command("analyze_last_100"))
async def analyze_last_100(message: Message):
    """Analyze last 100 messages"""
    await handle_analysis_command(message, "last_100")


@dp.message(Command("analyze_last_24h"))
async def analyze_last_24h(message: Message):
    """Analyze messages from last 24 hours"""
    await handle_analysis_command(message, "last_24h")


@dp.message(Command("add_user"))
async def add_user_command(message: Message):
    """Add user to authorized list (main admin only)"""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(Config.MESSAGES["private_chat_only"])
        return
    
    if not message.from_user:
        return
    
    user_id = message.from_user.id
    
    # Check if user is main admin
    if not is_main_admin(user_id):
        await message.answer(Config.MESSAGES["main_admin_only_add"])
        return
    
    # Check if this is a reply to someone's message
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user
        new_user_id = target_user.id
        username = target_user.username or target_user.first_name or Config.MESSAGES["default_username"]
        
        if add_authorized_user(new_user_id):
            await message.answer(Config.MESSAGES["user_added"].format(username=username, user_id=new_user_id))
            logger.info(f"User {new_user_id} (@{username}) added to authorized list by {user_id}")
        else:
            await message.answer(Config.MESSAGES["user_already_added"].format(username=username, user_id=new_user_id))
        return
    
    # Parse user ID or username from command
    try:
        command_parts = (message.text or "").split()
        if len(command_parts) != 2:
            await message.answer(Config.MESSAGES["add_user_usage"])
            return
        
        user_input = command_parts[1]
        
        # If it starts with @, it's a username
        if user_input.startswith('@'):
            username = user_input[1:]  # Remove @
            try:
                # Try to get user info by username
                chat_member = await bot.get_chat_member(message.chat.id, username)
                new_user_id = chat_member.user.id
                
                if add_authorized_user(new_user_id):
                    await message.answer(Config.MESSAGES["user_added"].format(username=username, user_id=new_user_id))
                    logger.info(f"User {new_user_id} (@{username}) added to authorized list by {user_id}")
                else:
                    await message.answer(Config.MESSAGES["user_already_added"].format(username=username, user_id=new_user_id))
                    
            except Exception as e:
                await message.answer(Config.MESSAGES["user_not_found"].format(username=username))
                logger.error(f"Error finding user @{username}: {e}")
        else:
            # Try to parse as numeric ID
            new_user_id = int(user_input)
            
            if add_authorized_user(new_user_id):
                await message.answer(Config.MESSAGES["user_added_by_id"].format(user_id=new_user_id))
                logger.info(f"User {new_user_id} added to authorized list by {user_id}")
            else:
                await message.answer(Config.MESSAGES["user_already_added_by_id"].format(user_id=new_user_id))
            
    except ValueError:
        await message.answer(Config.MESSAGES["invalid_format"])
    except Exception as e:
        await message.answer(Config.MESSAGES["error_adding_user"].format(error=str(e)))
        logger.error(f"Error adding user: {e}")


@dp.message(Command("remove_user"))
async def remove_user_command(message: Message):
    """Remove user from authorized list (main admin only)"""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(Config.MESSAGES["private_chat_only"])
        return
    
    if not message.from_user:
        return
    
    user_id = message.from_user.id
    
    # Check if user is main admin
    if not is_main_admin(user_id):
        await message.answer(Config.MESSAGES["main_admin_only_remove"])
        return
    
    # Check if this is a reply to someone's message
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user
        target_user_id = target_user.id
        username = target_user.username or target_user.first_name or Config.MESSAGES["default_username"]
        
        if remove_authorized_user(target_user_id):
            await message.answer(Config.MESSAGES["user_removed"].format(username=username, user_id=target_user_id))
            logger.info(f"User {target_user_id} (@{username}) removed from authorized list by {user_id}")
        else:
            await message.answer(Config.MESSAGES["user_cannot_remove"].format(username=username))
        return
    
    # Parse user ID or username from command
    try:
        command_parts = (message.text or "").split()
        if len(command_parts) != 2:
            await message.answer(Config.MESSAGES["remove_user_usage"])
            return
        
        user_input = command_parts[1]
        
        # If it starts with @, it's a username
        if user_input.startswith('@'):
            username = user_input[1:]  # Remove @
            try:
                # Try to get user info by username
                chat_member = await bot.get_chat_member(message.chat.id, username)
                target_user_id = chat_member.user.id
                
                if remove_authorized_user(target_user_id):
                    await message.answer(Config.MESSAGES["user_removed"].format(username=username, user_id=target_user_id))
                    logger.info(f"User {target_user_id} (@{username}) removed from authorized list by {user_id}")
                else:
                    await message.answer(Config.MESSAGES["user_cannot_remove_by_id"].format(username=username, user_id=target_user_id))
                    
            except Exception as e:
                await message.answer(Config.MESSAGES["user_not_found"].format(username=username))
                logger.error(f"Error finding user @{username}: {e}")
        else:
            # Try to parse as numeric ID
            target_user_id = int(user_input)
            
            if remove_authorized_user(target_user_id):
                await message.answer(Config.MESSAGES["user_removed_by_id"].format(user_id=target_user_id))
                logger.info(f"User {target_user_id} removed from authorized list by {user_id}")
            else:
                await message.answer(Config.MESSAGES["user_cannot_remove_by_id"].format(username="", user_id=target_user_id))
            
    except ValueError:
        await message.answer(Config.MESSAGES["invalid_format"])
    except Exception as e:
        await message.answer(Config.MESSAGES["error_removing_user"].format(error=str(e)))
        logger.error(f"Error removing user: {e}")


@dp.message(Command("list_users"))
async def list_users_command(message: Message):
    """Show list of authorized users (main admin only)"""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(Config.MESSAGES["private_chat_only"])
        return
    
    if not message.from_user:
        return
    
    user_id = message.from_user.id
    
    # Check if user is main admin
    if not is_main_admin(user_id):
        await message.answer(Config.MESSAGES["main_admin_only_list"])
        return
    
    if not Config.AUTHORIZED_USERS:
        await message.answer(Config.MESSAGES["user_list_empty"])
        return
    
    user_list = ""
    for i, uid in enumerate(Config.AUTHORIZED_USERS):
        role = Config.MESSAGES["main_admin_role"] if i == 0 else ""
        user_list += Config.MESSAGES["user_list_item"].format(user_id=uid, role=role)
    
    user_list_text = Config.MESSAGES["user_list_template"].format(user_list=user_list)
    await safe_send_message(message, text=user_list_text, parse_mode='Markdown')


@dp.message(Command("clear_memory"))
async def clear_memory_command(message: Message):
    """Clear old messages from memory to free up RAM (main admin only)"""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(Config.MESSAGES["private_chat_only"])
        return
    
    if not message.from_user:
        return
    
    user_id = message.from_user.id
    
    # Check if user is main admin
    if not is_main_admin(user_id):
        await message.answer(Config.MESSAGES["main_admin_only_clear"])
        return
    
    # Get memory stats before clearing
    memory_stats_before = message_cache.get_memory_usage_stats()
    
    # Clear old messages from memory
    message_cache.clear_old_messages_from_memory()
    
    # Get memory stats after clearing
    memory_stats_after = message_cache.get_memory_usage_stats()
    
    cleared_messages = memory_stats_before['total_messages_in_memory'] - memory_stats_after['total_messages_in_memory']
    
    stats_text = Config.MESSAGES["memory_cleared_template"].format(
        before_messages=memory_stats_before['total_messages_in_memory'],
        before_chats=memory_stats_before['total_chats_in_memory'],
        after_messages=memory_stats_after['total_messages_in_memory'],
        after_chats=memory_stats_after['total_chats_in_memory'],
        cleared_messages=cleared_messages,
        freed_memory=cleared_messages * 0.5
    )
    
    await safe_send_message(message, text=stats_text, parse_mode='Markdown')


@dp.message(Command("chat_stats"))
async def chat_stats_command(message: Message):
    """Show chat cache statistics (authorized users only)"""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(Config.MESSAGES["private_chat_only"])
        return
    
    if not message.from_user:
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Check if user is authorized
    if not is_user_authorized(user_id):
        await message.answer(Config.MESSAGES["not_authorized"])
        return
    
    # Get cache statistics
    cache_stats = message_cache.get_chat_stats(chat_id)
    memory_stats = message_cache.get_memory_usage_stats()
    
    # Format timestamps
    oldest_message = cache_stats['oldest_message'].strftime('%Y-%m-%d %H:%M') if cache_stats['oldest_message'] else Config.MESSAGES["no_messages"]
    newest_message = cache_stats['newest_message'].strftime('%Y-%m-%d %H:%M') if cache_stats['newest_message'] else Config.MESSAGES["no_messages"]
    
    # Determine warning message
    if cache_stats['total_messages'] == 0:
        warning_message = Config.MESSAGES["empty_cache_warning"]
    elif cache_stats['total_messages'] < 10:
        warning_message = Config.MESSAGES["low_messages_warning"]
    else:
        warning_message = ""
    
    stats_text = Config.MESSAGES["chat_stats_template"].format(
        chat_title=message.chat.title,
        total_messages=cache_stats['total_messages'],
        memory_messages=len(message_cache.chats.get(chat_id, [])),
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
    """Analyze personal communication style (authorized users only)"""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(Config.MESSAGES["private_chat_only"])
        return
    
    if not message.from_user:
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    username = message.from_user.username or message.from_user.first_name or "Пользователь"
    
    # Check if user is authorized
    if not is_user_authorized(user_id):
        await message.answer(Config.MESSAGES["not_authorized"])
        return
    
    # Check rate limiting
    if not check_rate_limit(user_id):
        await message.answer(
            Config.MESSAGES["rate_limit"].format(rate_limit=Config.RATE_LIMIT_SECONDS)
        )
        return
    
    # Show thinking message
    thinking_msg = await message.answer(Config.MESSAGES["analyzing_communication"])
    
    try:
        # Get user's messages
        user_messages = message_cache.get_user_messages(chat_id, user_id)
        
        # Get user's interactions with others
        interactions = message_cache.get_user_interactions(chat_id, user_id)
        
        if not user_messages:
            await safe_edit_message(
                thinking_msg,
                Config.MESSAGES["no_messages_for_analysis"]
            )
            return
        
        # Perform personal analysis
        analysis_result = await ai_analyzer.analyze_user_communication(
            user_messages, interactions, username
        )
        
        # Delete thinking message and send private analysis
        await thinking_msg.delete()
        
        # Send analysis privately to user
        await safe_send_message(
            bot,
            chat_id=user_id,
            text=analysis_result,
            parse_mode='Markdown'
        )
        
        # Confirm in group chat
        await message.answer(
            Config.MESSAGES["analysis_sent_private"].format(username=username)
        )
        
        logger.info(f"Personal analysis completed for user {user_id} in chat {chat_id}")
        
    except Exception as e:
        await safe_edit_message(thinking_msg, Config.MESSAGES["analysis_error"].format(error=str(e)))
        logger.error(f"Personal analysis error: {e}")


@dp.message(Command("analyze_user"))
async def analyze_user_command(message: Message):
    """Analyze specific user's communication style (authorized users only)"""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(Config.MESSAGES["private_chat_only"])
        return
    
    if not message.from_user:
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Check if user is authorized
    if not is_user_authorized(user_id):
        await message.answer(Config.MESSAGES["not_authorized"])
        return
    
    # Check rate limiting
    if not check_rate_limit(user_id):
        await message.answer(
            Config.MESSAGES["rate_limit"].format(rate_limit=Config.RATE_LIMIT_SECONDS)
        )
        return
    
    target_user_id = None
    target_username = None
    
    # Check if replying to a message
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user_id = message.reply_to_message.from_user.id
        target_username = message.reply_to_message.from_user.username or message.reply_to_message.from_user.first_name or Config.MESSAGES["default_username"]
    else:
        # Parse username from command
        command_parts = (message.text or "").split()
        if len(command_parts) < 2:
            await message.answer(Config.MESSAGES["analyze_user_usage"])
            return
        
        user_input = command_parts[1]
        if user_input.startswith('@'):
            username = user_input[1:]
            try:
                # Try to find user by username in cached messages
                all_messages = message_cache.get_last_n_messages(chat_id, 1000)
                for msg in all_messages:
                    if msg.get('username', '').lower() == username.lower():
                        target_user_id = msg['user_id']
                        target_username = msg['username']
                        break
                
                if not target_user_id:
                    await message.answer(
                        f"❌ Пользователь @{username} не найден в кеше сообщений этого чата. "
                        "Ответьте на сообщение пользователя командой /analyze_user."
                    )
                    return
            except Exception as e:
                await message.answer(f"❌ Ошибка поиска пользователя: {str(e)}")
                return
        else:
            await message.answer("❌ Неверный формат. Используйте @username.")
            return
    
    if not target_user_id:
        await message.answer("❌ Не удалось определить пользователя для анализа.")
        return
    
    # Show thinking message
    thinking_msg = await message.answer(f"🤔 Анализирую стиль коммуникации {target_username}...")
    
    try:
        # Get target user's messages
        user_messages = message_cache.get_user_messages(chat_id, target_user_id)
        
        # Get target user's interactions with others
        interactions = message_cache.get_user_interactions(chat_id, target_user_id)
        
        if not user_messages:
            await safe_edit_message(
                thinking_msg,
                f"❌ Нет доступных сообщений от {target_username} для анализа."
            )
            return
        
        # Perform personal analysis
        analysis_result = await ai_analyzer.analyze_user_communication(
            user_messages, interactions, target_username
        )
        
        # Delete thinking message and send private analysis
        await thinking_msg.delete()
        
        # Send analysis privately to requesting user
        await safe_send_message(
            bot,
            chat_id=user_id,
            text=analysis_result,
            parse_mode='Markdown'
        )
        
        # Confirm in group chat
        await message.answer(
            f"✅ Анализ {target_username} отправлен в личные сообщения."
        )
        
        logger.info(f"User analysis completed for target {target_user_id} by user {user_id} in chat {chat_id}")
        
    except Exception as e:
        await safe_edit_message(thinking_msg, f"❌ Ошибка при анализе: {str(e)}")
        logger.error(f"User analysis error: {e}")


@dp.message(Command("analyze_user_all"))
async def analyze_user_all_command(message: Message):
    """Analyze specific user's communication style across all chats (authorized users only)"""
    if not message.from_user:
        return
    
    user_id = message.from_user.id
    is_private_chat = message.chat.type == ChatType.PRIVATE
    
    # Check if user is authorized
    if not is_user_authorized(user_id):
        await message.answer("❌ У вас нет прав для использования этой команды.")
        return
    
    # Check rate limiting
    if not check_rate_limit(user_id):
        await message.answer(
            f"⏱️ Подождите {Config.RATE_LIMIT_SECONDS} секунд между командами анализа."
        )
        return
    
    target_user_id = None
    target_username = None
    
    # Check if replying to a message (only in group chats)
    if not is_private_chat and message.reply_to_message and message.reply_to_message.from_user:
        target_user_id = message.reply_to_message.from_user.id
        target_username = message.reply_to_message.from_user.username or message.reply_to_message.from_user.first_name or "Пользователь"
    else:
        # Parse username or user_id from command
        command_parts = (message.text or "").split()
        if len(command_parts) < 2:
            usage_text = "❌ Использование:\n• `/analyze_user_all @username` - анализ по имени из всех чатов"
            if not is_private_chat:
                usage_text += "\n• Ответьте на сообщение пользователя командой `/analyze_user_all`"
            usage_text += "\n• `/analyze_user_all <user_id>` - анализ по числовому ID пользователя"
            await message.answer(usage_text)
            return
        
        user_input = command_parts[1]
        if user_input.startswith('@'):
            # Username search
            username = user_input[1:]
            try:
                # Try to find user by username in cached messages from all chats
                for chat_id in message_cache.get_all_chats():
                    all_messages = message_cache.get_last_n_messages(chat_id, 1000)
                    for msg in all_messages:
                        if msg.get('username', '').lower() == username.lower():
                            target_user_id = msg['user_id']
                            target_username = msg['username']
                            break
                    if target_user_id:
                        break
                
                if not target_user_id:
                    await message.answer(
                        f"❌ Пользователь @{username} не найден в кеше сообщений ни в одном из чатов."
                    )
                    return
            except Exception as e:
                await message.answer(f"❌ Ошибка поиска пользователя: {str(e)}")
                return
        elif user_input.isdigit():
            # User ID search
            target_user_id = int(user_input)
            # Try to find username in cache
            for chat_id in message_cache.get_all_chats():
                all_messages = message_cache.get_last_n_messages(chat_id, 1000)
                for msg in all_messages:
                    if msg.get('user_id') == target_user_id:
                        target_username = msg.get('username', f"User_{target_user_id}")
                        break
                if target_username:
                    break
            
            if not target_username:
                target_username = f"User_{target_user_id}"
        else:
            await message.answer(
                "❌ Неверный формат. Используйте @username, числовой user_id" + 
                (" или ответьте на сообщение пользователя." if not is_private_chat else ".")
            )
            return
    
    # Show thinking message
    thinking_msg = await message.answer("🤔 Анализирую стиль коммуникации из всех чатов...")
    
    try:
        # Get user's messages from all chats
        user_messages = message_cache.get_user_messages_all_chats(target_user_id)
        
        # Get user's interactions with others from all chats
        interactions = message_cache.get_user_interactions_all_chats(target_user_id)
        
        # Get user stats across all chats
        user_stats = message_cache.get_user_chat_stats(target_user_id)
        
        if not user_messages:
            await thinking_msg.edit_text(
                f"❌ Нет доступных сообщений от {target_username} для анализа из всех чатов. "
                "Пользователь еще не отправлял сообщения после добавления бота в чаты."
            )
            return
        
        # Perform personal analysis
        analysis_result = await ai_analyzer.analyze_user_communication(
            user_messages, interactions, target_username
        )
        
        # Add cross-chat statistics to the analysis
        stats_summary = (
            f"\n\n📊 **Статистика по всем чатам:**\n"
            f"• Всего сообщений: {user_stats['total_messages']}\n"
            f"• Чатов с активностью: {user_stats['chats_count']}\n"
        )
        
        if user_stats['oldest_message'] and user_stats['newest_message']:
            stats_summary += (
                f"• Период активности: {user_stats['oldest_message'].strftime('%Y-%m-%d')} - "
                f"{user_stats['newest_message'].strftime('%Y-%m-%d')}\n"
            )
        
        # Combine analysis with statistics
        full_analysis = analysis_result + stats_summary
        
        # Delete thinking message
        await thinking_msg.delete()
        
        if is_private_chat:
            # In private chat, send analysis directly to this chat
            await safe_send_message(
                message,
                text=full_analysis,
                parse_mode='Markdown'
            )
        else:
            # In group chat, send analysis privately to requesting user
            await safe_send_message(
                bot,
                chat_id=user_id,
                text=full_analysis,
                parse_mode='Markdown'
            )
            
            # Confirm in group chat
            await message.answer(
                f"✅ Анализ {target_username} из всех чатов отправлен в личные сообщения.\n"
                f"📊 Проанализировано {user_stats['total_messages']} сообщений из {user_stats['chats_count']} чатов."
            )
        
        logger.info(f"Cross-chat user analysis completed for target {target_user_id} by user {user_id}")        
        
    except Exception as e:
        await safe_edit_message(thinking_msg, f"❌ Ошибка при анализе: {str(e)}")
        logger.error(f"Cross-chat user analysis error: {e}")

@dp.message(Command("conflict"))
async def cmd_conflict(message: Message):
    """Анализ конфликта в последних сообщениях"""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("❌ Эта команда работает только в групповых чатах.")
        return
    
    if not message.from_user:
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Rate limiting
    if not check_rate_limit(user_id):
        await message.answer(
            Config.MESSAGES["rate_limit"].format(rate_limit=Config.RATE_LIMIT_SECONDS)
        )
        return
    
    # Получаем последние 100 сообщений (можно изменить число)
    messages = message_cache.get_last_n_messages(chat_id, 100)
    if not messages:
        await message.answer("❌ Недостаточно сообщений для анализа. Подождите, пока накопится история.")
        return
    
    thinking_msg = await message.answer("🔍 Анализирую последние сообщения на предмет конфликта... ⏳")
    
    try:
        analysis = await ai_analyzer.analyze_conflict(messages)
        await thinking_msg.delete()
        await safe_send_message(message, text=f"📝 *Анализ конфликта:*\n\n{analysis}", parse_mode='Markdown')
    except Exception as e:
        await safe_edit_message(thinking_msg, f"❌ Ошибка: {str(e)}")
        logger.error(f"Conflict analysis error: {e}")


@dp.message(Command("digest"))
async def cmd_digest(message: Message):
    """Дайджест полезных советов за последние 24 часа"""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("❌ Эта команда работает только в групповых чатах.")
        return
    
    if not message.from_user:
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if not check_rate_limit(user_id):
        await message.answer(
            Config.MESSAGES["rate_limit"].format(rate_limit=Config.RATE_LIMIT_SECONDS)
        )
        return
    
    # Сообщения за последние 24 часа
    since = datetime.now() - timedelta(hours=24)
    messages = message_cache.get_messages_since(chat_id, since)
    if not messages:
        await message.answer("❌ За последние 24 часа не было сообщений.")
        return
    
    thinking_msg = await message.answer("🔍 Собираю полезные советы за последние 24 часа... ⏳")
    
    try:
        tips = await ai_analyzer.analyze_tips(messages)
        await thinking_msg.delete()
        await safe_send_message(message, text=f"💡 *Дайджест советов и идей:*\n\n{tips}", parse_mode='Markdown')
    except Exception as e:
        await safe_edit_message(thinking_msg, f"❌ Ошибка: {str(e)}")
        logger.error(f"Tips analysis error: {e}")         


async def handle_analysis_command(message: Message, analysis_type: str):
    """Handle analysis commands with common logic"""
    # Only work in group chats
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Команды анализа работают только в групповых чатах.")
        return
    
    if not message.from_user:
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Check if user is authorized
    if not is_user_authorized(user_id):
        await message.answer("❌ У вас нет прав для использования этой команды.")
        return
    
    # Check rate limiting
    if not check_rate_limit(user_id):
        await message.answer(
            f"⏱ Пожалуйста, подождите {Config.RATE_LIMIT_SECONDS} секунд между командами анализа."
        )
        return
    
    # Get messages based on analysis type
    if analysis_type == "last_100":
        messages = message_cache.get_last_n_messages(chat_id, 100)
        analysis_description = "последних 100 сообщений"
    elif analysis_type == "last_24h":
        messages = message_cache.get_messages_since(chat_id, datetime.now() - timedelta(hours=24))
        analysis_description = "сообщений за последние 24 часа"
    else:
        await message.answer("❌ Неизвестный тип анализа.")
        return
    
    if not messages:
        # Get cache stats to provide better feedback
        cache_stats = message_cache.get_chat_stats(chat_id)
        if cache_stats['total_messages'] == 0:
            await message.answer(
                "❌ Нет сообщений для анализа.\n\n"
                "🔍 **Возможные причины:**\n"
                "• Бот был добавлен недавно и еще не накопил сообщения\n"
                "• Боты в Telegram не видят историю до их добавления в чат\n"
                "• В чате пока нет текстовых сообщений (команды не считаются)\n\n"
                "💡 **Решение:** Подождите, пока участники напишут несколько сообщений после добавления бота."
            )
        else:
            await message.answer(
                f"❌ Недостаточно сообщений для анализа {analysis_description}.\n\n"
                f"📊 **Статистика чата:**\n"
                f"• Всего сообщений в кеше: {cache_stats['total_messages']}\n"
                f"• Активных пользователей: {cache_stats['unique_users']}\n"
                f"• Первое сообщение: {cache_stats['oldest_message'].strftime('%Y-%m-%d %H:%M') if cache_stats['oldest_message'] else 'Нет'}\n\n"
                f"💡 Попробуйте команду с меньшим количеством сообщений или подождите больше активности в чате."
            )
        return
    
    # Send notification in group chat
    await message.answer(
        f"🔍 Начинаю анализ {analysis_description} ({len(messages)} сообщений). "
        f"Отчет будет отправлен в личные сообщения."
    )
    
    # Send private notification about analysis start
    try:
        await bot.send_message(
            user_id,
            f"🔄 Анализирую {analysis_description} из чата '{message.chat.title}'. "
            "Это может занять несколько минут..."
        )
    except Exception as e:
        logger.error(f"Failed to send private notification: {e}")
        await message.answer("❌ Не удалось отправить уведомление в личные сообщения. Убедитесь, что вы начали диалог с ботом командой /start.")
        return
    
    # Perform analysis
    try:
        analysis_result = await ai_analyzer.analyze_messages(messages)
        
        # Send analysis result privately
        await safe_send_message(
            bot,
            chat_id=user_id,
            text=f"📊 **Анализ коммуникаций: {message.chat.title}**\n\n{analysis_result}",
            parse_mode='Markdown'
        )
        
        logger.info(f"Analysis completed for user {user_id} in chat {chat_id}")
        
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        await safe_send_message(
            bot,
            chat_id=user_id,
            text="❌ Произошла ошибка при анализе сообщений. Попробуйте позже или обратитесь к администратору бота.",
            parse_mode='Markdown'
        )


@dp.message(F.text & F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cache_group_message(message: Message):
    """Cache all text messages from group chats"""
    # Skip bot commands
    if message.text and message.text.startswith('/'):
        return
    
    # Skip if no user info or text
    if not message.from_user or not message.text:
        return
    
    # Cache the message
    message_cache.add_message(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        username=message.from_user.username or message.from_user.first_name or "Пользователь",
        text=message.text,
        timestamp=datetime.now()
    )
    
    # Log every 10th message for monitoring
    cache_stats = message_cache.get_chat_stats(message.chat.id)
    if cache_stats['total_messages'] % 10 == 0:
        logger.info(f"Chat {message.chat.id} now has {cache_stats['total_messages']} cached messages from {cache_stats['unique_users']} users")

async def handle_update(update_data):
    """Обработка обновлений для вебхуков"""
    from aiogram.types import Update
    update = Update(**update_data)
    await dp.feed_update(bot, update) 

async def main():
    """Main function to start the bot"""
    logger.info("Starting Communication Coach Bot...")
    
    # Check required environment variables
    if not Config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment variables")
        return
    
    if not Config.OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY not found in environment variables")
        return
    
    if not Config.AUTHORIZED_USERS:
        logger.warning("No authorized users configured. Set AUTHORIZED_USERS environment variable with comma-separated user IDs.")
        logger.warning("Example: AUTHORIZED_USERS=123456789,987654321")
    else:
        logger.info(f"Authorized users: {Config.AUTHORIZED_USERS}")
        logger.info(f"Main admin: {Config.AUTHORIZED_USERS[0]}")
    
    try:
        # Start polling
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Bot failed to start: {e}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    # Для локального запуска (polling)
    if os.getenv("VERCEL_ENV") != "1":
        asyncio.run(main()) 
