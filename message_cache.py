import logging
import sqlite3
from collections import deque, defaultdict
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

from config import Config

logger = logging.getLogger(__name__)


class MessageCache:
    """Memory-optimized cache for storing chat messages"""
    
    def __init__(self, max_size: int = 1000, memory_cache_size: int = 50):
        """
        Initialize message cache
        
        Args:
            max_size: Maximum number of messages to store per chat in database
            memory_cache_size: Maximum number of messages to keep in memory per chat
        """
        self.max_size = max_size
        self.memory_cache_size = memory_cache_size
        # Dictionary of chat_id -> deque of messages (smaller in-memory cache)
        self.chats: Dict[int, deque] = defaultdict(lambda: deque(maxlen=memory_cache_size))
        
        # Initialize SQLite connection for persistence
        self.db_path = Config.DB_PATH
        
        # На Vercel нужно использовать check_same_thread=True
        # потому что база во временной папке
        if Config.IS_VERCEL:
            # На Vercel всё работает в одном потоке
            self.conn = sqlite3.connect(self.db_path, check_same_thread=True)
            logger.info("Running on Vercel - using single-thread mode for database")
        else:
            # На локальном компьютере можно использовать несколько потоков
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        
        self.conn.row_factory = sqlite3.Row
        self._init_db()
        logger.info(f"MessageCache initialized with max_size={max_size}, memory_cache_size={memory_cache_size}")
        logger.info(f"SQLite persistence enabled at {self.db_path}")
        
        # Добавим предупреждение для Vercel
        if Config.IS_VERCEL:
            logger.warning("⚠️ Running on Vercel - database is temporary and will be lost on redeploy!")
            logger.warning("   Messages will be stored only in memory between restarts.") 

    def _init_db(self):
        cur = self.conn.cursor()
        cur.execute(Config.SQL_QUERIES["create_messages_table"])
        cur.execute(Config.SQL_QUERIES["create_index_chat_time"])
        cur.execute(Config.SQL_QUERIES["create_index_user_time"])
        self.conn.commit()
    
    def add_message(self, chat_id: int, user_id: int, username: str, text: str, timestamp: datetime):
        """
        Add a message to the cache
        
        Args:
            chat_id: Telegram chat ID
            user_id: Telegram user ID
            username: Username or first name
            text: Message text
            timestamp: When the message was sent
        """
        message = {
            'chat_id': chat_id,
            'user_id': user_id,
            'username': username,
            'text': text,
            'timestamp': timestamp
        }
        
        # Write-through to in-memory cache
        self.chats[chat_id].append(message)
        
        # Persist to SQLite
        try:
            cur = self.conn.cursor()
            cur.execute(
                Config.SQL_QUERIES["insert_message"],
                (chat_id, user_id, username, text, self._ts_to_str(timestamp))
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to persist message to DB: {e}")
        
        # Periodically clear old messages from memory to save RAM
        # Clear every 20 messages to balance performance and memory usage
        if len(self.chats[chat_id]) % 20 == 0:
            self.clear_old_messages_from_memory(chat_id)
        
        logger.debug(f"Added message to chat {chat_id}: {len(self.chats[chat_id])} messages in memory")
    
    def get_last_n_messages(self, chat_id: int, n: int) -> List[Dict[str, Any]]:
        """
        Get last N messages from a chat
        
        Args:
            chat_id: Telegram chat ID
            n: Number of messages to retrieve
            
        Returns:
            List of message dictionaries, ordered by timestamp (oldest first)
        """
        # Prefer DB for retrieval to include persisted history
        try:
            cur = self.conn.cursor()
            cur.execute(
                Config.SQL_QUERIES["get_last_n_messages"],
                (chat_id, n)
            )
            rows = cur.fetchall()
            # Reverse to chronological order (oldest first)
            messages = [self._row_to_message(row) for row in reversed(rows)]
            logger.info(f"Retrieved {len(messages)} messages from chat {chat_id} (requested: {n})")
            return messages
        except Exception as e:
            logger.error(f"DB error in get_last_n_messages: {e}")
            # Fallback to in-memory cache
            if chat_id not in self.chats:
                logger.warning(f"No messages found for chat {chat_id}")
                return []
            messages = list(self.chats[chat_id])
            return messages[-n:] if len(messages) >= n else messages
    
    def get_messages_since(self, chat_id: int, since_time: datetime) -> List[Dict[str, Any]]:
        """
        Get messages from a chat since a specific time
        
        Args:
            chat_id: Telegram chat ID
            since_time: Get messages newer than this timestamp
            
        Returns:
            List of message dictionaries, ordered by timestamp (oldest first)
        """
        try:
            cur = self.conn.cursor()
            cur.execute(
                Config.SQL_QUERIES["get_messages_since"],
                (chat_id, self._ts_to_str(since_time))
            )
            rows = cur.fetchall()
            messages = [self._row_to_message(row) for row in rows]
            logger.info(f"Retrieved {len(messages)} messages from chat {chat_id} since {since_time}")
            return messages
        except Exception as e:
            logger.error(f"DB error in get_messages_since: {e}")
            if chat_id not in self.chats:
                logger.warning(f"No messages found for chat {chat_id}")
                return []
            return [m for m in self.chats[chat_id] if m['timestamp'] >= since_time]
    
    def get_chat_stats(self, chat_id: int) -> Dict[str, Any]:
        """
        Get statistics for a chat
        
        Args:
            chat_id: Telegram chat ID
            
        Returns:
            Dictionary with chat statistics
        """
        try:
            cur = self.conn.cursor()
            cur.execute(Config.SQL_QUERIES["get_chat_stats_count"], (chat_id,))
            row = cur.fetchone()
            cur.execute(Config.SQL_QUERIES["get_chat_stats_users"], (chat_id,))
            row2 = cur.fetchone()
            total = int(row["cnt"]) if row and row["cnt"] is not None else 0
            oldest_ts = self._str_to_ts(row["oldest"]) if row and row["oldest"] else None
            newest_ts = self._str_to_ts(row["newest"]) if row and row["newest"] else None
            unique_users = int(row2["users"]) if row2 and row2["users"] is not None else 0
            return {
                'total_messages': total,
                'unique_users': unique_users,
                'oldest_message': oldest_ts,
                'newest_message': newest_ts
            }
        except Exception as e:
            logger.error(f"DB error in get_chat_stats: {e}")
            if chat_id not in self.chats:
                return {
                    'total_messages': 0,
                    'unique_users': 0,
                    'oldest_message': None,
                    'newest_message': None
                }
            messages = list(self.chats[chat_id])
            unique_users = set(msg['user_id'] for msg in messages)
            return {
                'total_messages': len(messages),
                'unique_users': len(unique_users),
                'oldest_message': messages[0]['timestamp'] if messages else None,
                'newest_message': messages[-1]['timestamp'] if messages else None
            }
    
    def clear_chat(self, chat_id: int):
        """
        Clear all messages for a specific chat
        
        Args:
            chat_id: Telegram chat ID
        """
        if chat_id in self.chats:
            self.chats[chat_id].clear()
        try:
            cur = self.conn.cursor()
            cur.execute(Config.SQL_QUERIES["clear_chat"], (chat_id,))
            self.conn.commit()
            logger.info(f"Cleared all messages for chat {chat_id}")
        except Exception as e:
            logger.error(f"DB error while clearing chat {chat_id}: {e}")
    
    def get_all_chats(self) -> List[int]:
        """
        Get list of all chat IDs with cached messages
        
        Returns:
            List of chat IDs
        """
        chat_ids = set(self.chats.keys())
        try:
            cur = self.conn.cursor()
            cur.execute(Config.SQL_QUERIES["get_all_chats"])
            rows = cur.fetchall()
            for row in rows:
                chat_ids.add(int(row["chat_id"]))
        except Exception as e:
            logger.error(f"DB error in get_all_chats: {e}")
        return list(chat_ids)
    
    def get_user_messages(self, chat_id: int, user_id: int, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get messages from a specific user in a chat
        
        Args:
            chat_id: Telegram chat ID
            user_id: Specific user ID to get messages from
            limit: Maximum number of messages to return (most recent first)
            
        Returns:
            List of message dictionaries from the specified user
        """
        try:
            cur = self.conn.cursor()
            if limit:
                cur.execute(
                    Config.SQL_QUERIES["get_user_messages_with_limit"],
                    (chat_id, user_id, limit)
                )
                rows = list(reversed(cur.fetchall()))
            else:
                cur.execute(
                    Config.SQL_QUERIES["get_user_messages_without_limit"],
                    (chat_id, user_id)
                )
                rows = cur.fetchall()
            user_messages = [self._row_to_message(row) for row in rows]
            logger.info(f"Retrieved {len(user_messages)} messages from user {user_id} in chat {chat_id}")
            return user_messages
        except Exception as e:
            logger.error(f"DB error in get_user_messages: {e}")
            if chat_id not in self.chats:
                logger.warning(f"No messages found for chat {chat_id}")
                return []
            user_messages = [m for m in self.chats[chat_id] if m['user_id'] == user_id]
            if limit and len(user_messages) > limit:
                user_messages = user_messages[-limit:]
            return user_messages
    
    def get_user_interactions(self, chat_id: int, user_id: int, limit: Optional[int] = None) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get messages involving a specific user and their interactions with others
        
        Args:
            chat_id: Telegram chat ID
            user_id: User ID to analyze interactions for
            limit: Maximum number of messages to analyze per interaction partner
            
        Returns:
            Dictionary where keys are usernames and values are lists of interaction messages
        """
        if chat_id not in self.chats:
            logger.warning(f"No messages found for chat {chat_id}")
            return {}
        
        interactions = defaultdict(list)
        all_messages = list(self.chats[chat_id])
        
        # Group messages by interaction partners
        for i, message in enumerate(all_messages):
            if message['user_id'] == user_id:
                # Add user's own message
                interactions['self'].append(message)
                
                # Look for context messages around this message (before and after)
                context_range = 3  # Look 3 messages before and after
                start_idx = max(0, i - context_range)
                end_idx = min(len(all_messages), i + context_range + 1)
                
                for j in range(start_idx, end_idx):
                    context_msg = all_messages[j]
                    if context_msg['user_id'] != user_id:
                        partner_name = context_msg['username']
                        # Add this as an interaction
                        interactions[partner_name].append({
                            'type': 'interaction',
                            'user_message': message if j > i else None,
                            'partner_message': context_msg,
                            'timestamp': context_msg['timestamp']
                        })
        
        # Apply limit if specified for each interaction partner
        if limit:
            for partner in interactions:
                if len(interactions[partner]) > limit:
                    interactions[partner] = interactions[partner][-limit:]
        
        logger.info(f"Retrieved interactions for user {user_id} with {len(interactions)} partners in chat {chat_id}")
        return dict(interactions)
    
    def get_communication_partners(self, chat_id: int, user_id: int) -> Dict[str, Dict[str, Any]]:
        """
        Get statistics about who a user communicates with most
        
        Args:
            chat_id: Telegram chat ID
            user_id: User ID to analyze
            
        Returns:
            Dictionary with partner statistics
        """
        if chat_id not in self.chats:
            return {}
        
        partners: Dict[str, Dict[str, Any]] = defaultdict(lambda: {'message_count': 0, 'user_id': None, 'last_interaction': None})
        user_messages = []
        
        all_messages = list(self.chats[chat_id])
        
        # Find all messages from the target user
        for i, message in enumerate(all_messages):
            if message['user_id'] == user_id:
                user_messages.append((i, message))
        
        # For each user message, find nearby messages from other users
        for msg_idx, user_msg in user_messages:
            # Look for messages within a conversation window
            window_size = 5
            start = max(0, msg_idx - window_size)
            end = min(len(all_messages), msg_idx + window_size + 1)
            
            for j in range(start, end):
                if j != msg_idx:  # Skip the user's own message
                    other_msg = all_messages[j]
                    if other_msg['user_id'] != user_id:
                        partner_name = other_msg['username']
                        partners[partner_name]['message_count'] = partners[partner_name]['message_count'] + 1
                        partners[partner_name]['user_id'] = other_msg['user_id']
                        partners[partner_name]['last_interaction'] = other_msg['timestamp']
        
        return dict(partners)
    
    def get_user_messages_all_chats(self, user_id: int, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get messages from a specific user across all chats
        
        Args:
            user_id: Specific user ID to get messages from
            limit: Maximum number of messages to return (most recent first)
            
        Returns:
            List of message dictionaries from the specified user across all chats
        """
        try:
            cur = self.conn.cursor()
            if limit:
                cur.execute(
                    Config.SQL_QUERIES["get_user_messages_all_chats_with_limit"],
                    (user_id, limit)
                )
                rows = list(reversed(cur.fetchall()))
            else:
                cur.execute(
                    Config.SQL_QUERIES["get_user_messages_all_chats_without_limit"],
                    (user_id,)
                )
                rows = cur.fetchall()
            all_user_messages = [self._row_to_message(row) for row in rows]
            logger.info(f"Retrieved {len(all_user_messages)} messages from user {user_id} across all chats")
            return all_user_messages
        except Exception as e:
            logger.error(f"DB error in get_user_messages_all_chats: {e}")
            # Fallback to in-memory
            all_user_messages: List[Dict[str, Any]] = []
            for chat_id in self.chats:
                for message in self.chats[chat_id]:
                    if message['user_id'] == user_id:
                        all_user_messages.append(message)
            all_user_messages.sort(key=lambda x: x['timestamp'])
            if limit and len(all_user_messages) > limit:
                all_user_messages = all_user_messages[-limit:]
            return all_user_messages
    
    def get_user_interactions_all_chats(self, user_id: int, limit: Optional[int] = None) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get messages involving a specific user and their interactions with others across all chats
        
        Args:
            user_id: User ID to analyze interactions for
            limit: Maximum number of messages to analyze per interaction partner
            
        Returns:
            Dictionary where keys are usernames and values are lists of interaction messages
        """
        # Keep interaction logic based on in-memory cache to preserve window context
        interactions = defaultdict(list)
        for chat_id in self.get_all_chats():
            all_messages = list(self.get_last_n_messages(chat_id, self.max_size))
            for i, message in enumerate(all_messages):
                if message['user_id'] == user_id:
                    interactions['self'].append(message)
                    context_range = 3
                    start_idx = max(0, i - context_range)
                    end_idx = min(len(all_messages), i + context_range + 1)
                    for j in range(start_idx, end_idx):
                        context_msg = all_messages[j]
                        if context_msg['user_id'] != user_id:
                            partner_name = context_msg['username']
                            interactions[partner_name].append({
                                'type': 'interaction',
                                'user_message': message if j > i else None,
                                'partner_message': context_msg,
                                'timestamp': context_msg['timestamp'],
                                'chat_id': chat_id
                            })
        if limit:
            for partner in interactions:
                if len(interactions[partner]) > limit:
                    interactions[partner] = interactions[partner][-limit:]
        logger.info(f"Retrieved interactions for user {user_id} with {len(interactions)} partners across all chats")
        return dict(interactions)
    
    def get_user_chat_stats(self, user_id: int) -> Dict[str, Any]:
        """
        Get statistics about user's presence across all chats
        
        Args:
            user_id: User ID to analyze
            
        Returns:
            Dictionary with user statistics across all chats
        """
        try:
            cur = self.conn.cursor()
            cur.execute(Config.SQL_QUERIES["get_user_chat_stats_count"], (user_id,))
            row = cur.fetchone()
            cur.execute(Config.SQL_QUERIES["get_user_chat_stats_chats"], (user_id,))
            chat_rows = cur.fetchall()
            total = int(row["cnt"]) if row and row["cnt"] is not None else 0
            oldest_ts = self._str_to_ts(row["oldest"]) if row and row["oldest"] else None
            newest_ts = self._str_to_ts(row["newest"]) if row and row["newest"] else None
            chat_ids = [int(r["chat_id"]) for r in chat_rows] if chat_rows else []
            return {
                'total_messages': total,
                'chats_count': len(chat_ids),
                'chat_ids': chat_ids,
                'oldest_message': oldest_ts,
                'newest_message': newest_ts
            }
        except Exception as e:
            logger.error(f"DB error in get_user_chat_stats: {e}")
            # Fallback to in-memory
            stats = {
                'total_messages': 0,
                'chats_count': 0,
                'chat_ids': [],
                'oldest_message': None,
                'newest_message': None
            }
            all_user_messages: List[Dict[str, Any]] = []
            for chat_id in self.chats:
                chat_messages = []
                for message in self.chats[chat_id]:
                    if message['user_id'] == user_id:
                        chat_messages.append(message)
                        all_user_messages.append(message)
                if chat_messages:
                    stats['chats_count'] += 1
                    stats['chat_ids'].append(chat_id)
            stats['total_messages'] = len(all_user_messages)
            if all_user_messages:
                all_user_messages.sort(key=lambda x: x['timestamp'])
                stats['oldest_message'] = all_user_messages[0]['timestamp']
                stats['newest_message'] = all_user_messages[-1]['timestamp']
            return stats

    def _row_to_message(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'chat_id': int(row['chat_id']),
            'user_id': int(row['user_id']),
            'username': row['username'],
            'text': row['text'],
            'timestamp': self._str_to_ts(row['timestamp'])
        }

    def _ts_to_str(self, ts: datetime) -> str:
        # Use ISO format for lexical ordering compatibility
        return ts.strftime('%Y-%m-%d %H:%M:%S')

    def _str_to_ts(self, s: str) -> datetime:
        # Parse ISO-like string
        try:
            return datetime.strptime(s, '%Y-%m-%d %H:%M:%S')
        except Exception:
            # Fallback to datetime.fromisoformat if possible
            try:
                return datetime.fromisoformat(s)
            except Exception:
                # Last resort: return current time to avoid crashes
                logger.error(f"Failed to parse timestamp: {s}")
                return datetime.now()
    
    def clear_old_messages_from_memory(self, chat_id: int = None):
        """
        Clear old messages from memory cache to free up RAM
        
        Args:
            chat_id: Specific chat to clear, or None to clear all chats
        """
        if chat_id is not None:
            if chat_id in self.chats:
                # Keep only the most recent messages in memory
                recent_messages = list(self.chats[chat_id])[-self.memory_cache_size:]
                self.chats[chat_id].clear()
                self.chats[chat_id].extend(recent_messages)
                logger.debug(f"Cleared old messages from memory for chat {chat_id}, kept {len(recent_messages)} recent messages")
        else:
            # Clear all chats
            for cid in list(self.chats.keys()):
                recent_messages = list(self.chats[cid])[-self.memory_cache_size:]
                self.chats[cid].clear()
                self.chats[cid].extend(recent_messages)
                logger.debug(f"Cleared old messages from memory for chat {cid}, kept {len(recent_messages)} recent messages")
    
    def get_memory_usage_stats(self) -> Dict[str, Any]:
        """
        Get statistics about memory usage
        
        Returns:
            Dictionary with memory usage statistics
        """
        total_messages_in_memory = sum(len(chat) for chat in self.chats.values())
        total_chats = len(self.chats)
        
        return {
            'total_messages_in_memory': total_messages_in_memory,
            'total_chats_in_memory': total_chats,
            'memory_cache_size': self.memory_cache_size,
            'max_db_size': self.max_size
        }
