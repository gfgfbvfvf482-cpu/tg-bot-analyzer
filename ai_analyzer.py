# -*- coding: utf-8 -*-
import json
import logging
import asyncio
from datetime import datetime
from typing import List, Dict, Any

import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold 
from config import Config

logger = logging.getLogger(__name__)


class CommunicationAnalyzer:
    """AI-powered communication analyzer using Google Gemini API"""

    def __init__(self):
        # ✅ ИСПРАВЛЕНО: не делаем сетевые вызовы в __init__
        # Раньше genai.list_models() вызывался здесь и мог уронить весь бот при старте,
        # если ключ неверный или нет интернета.
        # Теперь просто настраиваем ключ — проверка произойдёт при первом реальном запросе.
        
        if not Config.GEMINI_API_KEY:
            logger.error("❌ GEMINI_API_KEY не задан! Установите его в переменных окружения Vercel.")
        else:
            genai.configure(api_key=Config.GEMINI_API_KEY)
            logger.info("✅ Gemini API настроен")
        
        # Устанавливаем модель
        self.model_name = "models/gemini-2.5-flash"

    async def _call_gemini(self, system_prompt: str, user_prompt: str, 
                           temperature: float = 0.4, max_tokens: int = 3000, 
                           response_json: bool = True) -> str:
        """
        Вспомогательный метод для вызова Gemini API асинхронно.
        """
        # ✅ Проверяем ключ перед каждым вызовом
        if not Config.GEMINI_API_KEY:
            return "❌ GEMINI_API_KEY не задан. Добавьте его в настройках Vercel → Settings → Environment Variables."

        try:
            from google.generativeai.types import HarmCategory, HarmBlockThreshold
            
            safety_settings = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }
            
            model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=system_prompt,
                safety_settings=safety_settings
            )

            generation_config = {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
            if response_json:
                generation_config["response_mime_type"] = "application/json"

            # ✅ Запускаем синхронный вызов в отдельном потоке (правильный способ для async)
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: model.generate_content(
                    user_prompt,
                    generation_config=generation_config,
                )
            )

            if response.prompt_feedback and response.prompt_feedback.block_reason:
                block_reason = response.prompt_feedback.block_reason
                logger.warning(f"Запрос заблокирован: {block_reason}")
                return f"⚠️ Запрос заблокирован API: {block_reason}. Попробуйте смягчить формулировки."

            return response.text
            
        except Exception as e:
            logger.error(f"Gemini API call failed: {e}")
            
            error_message = str(e)
            if "404" in error_message and "model" in error_message:
                return "❌ Модель не найдена. Проверьте правильность имени модели."
            elif "API key" in error_message or "401" in error_message:
                return "❌ Неверный GEMINI_API_KEY. Проверьте ключ в настройках Vercel."
            elif "quota" in error_message.lower() or "rate limit" in error_message.lower() or "429" in error_message:
                return "❌ Превышен лимит запросов к API. Подождите немного."
            else:
                return f"❌ Ошибка при обращении к AI: {error_message[:200]}"

    async def check_available_models(self):
        """Проверить, какие модели доступны"""
        try:
            models = genai.list_models()
            available = []
            for m in models:
                if 'generateContent' in m.supported_generation_methods:
                    available.append(m.name)
            logger.info(f"Доступные модели: {available}")
            return available
        except Exception as e:
            logger.error(f"Ошибка при получении списка моделей: {e}")
            return []

    async def analyze_messages(self, messages: List[Dict[str, Any]]) -> str:
        """Анализ групповых сообщений, возвращает отчёт."""
        if not messages:
            return "❌ Нет сообщений для анализа."

        try:
            formatted_messages = self._format_messages(messages)
            analysis_prompt = self._create_analysis_prompt(formatted_messages, len(messages))
            system_prompt = self._get_system_prompt()
            
            response_content = await self._call_gemini(
                system_prompt=system_prompt,
                user_prompt=analysis_prompt,
                temperature=0.4,
                max_tokens=3000,
                response_json=True
            )

            if not response_content:
                return "❌ Получен пустой ответ от AI."

            # ✅ Проверяем, не вернулась ли ошибка в виде текста (начинается с ❌)
            if response_content.startswith("❌") or response_content.startswith("⚠️"):
                return response_content

            analysis_json = json.loads(response_content)
            return self._format_analysis_report(analysis_json, len(messages))

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {e}")
            return "❌ Ошибка обработки ответа AI. Попробуйте позже."
        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return f"❌ Ошибка при анализе: {str(e)}"

    async def analyze_user_communication(
        self,
        user_messages: List[Dict[str, Any]],
        interactions: Dict[str, List[Dict[str, Any]]],
        username: str,
    ) -> str:
        """Персональный анализ для конкретного пользователя."""
        if not user_messages:
            return "❌ Нет сообщений пользователя для анализа."

        try:
            analysis_prompt = self._create_personal_analysis_prompt(
                user_messages, interactions, username)
            system_prompt = self._get_personal_analysis_system_prompt()
            
            response_content = await self._call_gemini(
                system_prompt=system_prompt,
                user_prompt=analysis_prompt,
                temperature=0.4,
                max_tokens=3500,
                response_json=True
            )

            if not response_content:
                return "❌ Получен пустой ответ от AI."

            if response_content.startswith("❌") or response_content.startswith("⚠️"):
                return response_content

            analysis_json = json.loads(response_content)
            return self._format_personal_analysis_report(analysis_json, username, len(user_messages))

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse personal analysis as JSON: {e}")
            return "❌ Ошибка обработки ответа AI."
        except Exception as e:
            logger.error(f"Personal analysis failed: {e}")
            return f"❌ Ошибка при анализе: {str(e)}"

    async def analyze_conflict(self, messages: List[Dict[str, Any]]) -> str:
        """Анализ конфликта в диалоге."""
        if not messages:
            return "❌ Нет сообщений для анализа."

        try:
            formatted_messages = self._format_messages(messages)
            system_prompt = """Ты - профессиональный медиатор. Проанализируй диалог и опиши структуру конфликта: 
- Стороны (никнеймы)
- Причина (из-за чего искра)
- Эскалация (как накалялось)
- Аргументы сторон (кто что говорил)
- Итог (помирились или нет)"""
            user_prompt = f"Вот диалог:\n{formatted_messages}\n\nОпиши структуру конфликта."

            response = await self._call_gemini(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.3,
                max_tokens=2000,
                response_json=False
            )
            return response
        except Exception as e:
            logger.error(f"Conflict analysis failed: {e}")
            return f"❌ Ошибка при анализе конфликта: {str(e)}"

    async def analyze_tips(self, messages: List[Dict[str, Any]]) -> str:
        """Выделение полезных советов из диалога."""
        if not messages:
            return "❌ Нет сообщений для анализа."

        try:
            formatted_messages = self._format_messages(messages)
            system_prompt = """Ты - редактор дайджеста. Выдели из переписки 3-5 самых ценных мыслей, советов или лайфхаков. 
Если это диалог (вопрос-ответ), опиши проблему и предложенное решение. Используй понятный язык."""
            user_prompt = f"Вот переписка:\n{formatted_messages}\n\nВыдели полезные советы и идеи."

            response = await self._call_gemini(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.3,
                max_tokens=2000,
                response_json=False
            )
            return response
        except Exception as e:
            logger.error(f"Tips analysis failed: {e}")
            return f"❌ Ошибка при выделении советов: {str(e)}"

    def _get_system_prompt(self) -> str:
        return Config.GROUP_ANALYSIS_SYSTEM_PROMPT

    def _get_personal_analysis_system_prompt(self) -> str:
        return Config.PERSONAL_ANALYSIS_SYSTEM_PROMPT

    def _format_messages(self, messages: List[Dict[str, Any]]) -> str:
        """Форматирует список сообщений в строку для AI."""
        lines = []
        for msg in messages:
            ts = msg.get("timestamp")
            if hasattr(ts, "strftime"):
                timestamp = ts.strftime("%Y-%m-%d %H:%M")
            else:
                timestamp = str(ts)
            username = msg.get("username", "Пользователь")
            text = msg.get("text", "")
            lines.append(f"[{timestamp}] {username}: {text}")
        return "\n".join(lines)

    def _create_analysis_prompt(self, formatted_messages: str, message_count: int) -> str:
        return Config.GROUP_ANALYSIS_USER_PROMPT_TEMPLATE.format(
            message_count=message_count,
            formatted_messages=formatted_messages
        )

    def _create_personal_analysis_prompt(
        self,
        user_messages: List[Dict[str, Any]],
        interactions: Dict[str, List[Dict[str, Any]]],
        username: str,
    ) -> str:
        user_msgs_formatted = []
        for msg in user_messages[-20:]:
            ts = msg.get("timestamp")
            if hasattr(ts, "strftime"):
                timestamp = ts.strftime("%Y-%m-%d %H:%M")
            else:
                timestamp = str(ts)
            text = msg.get("text", "")
            user_msgs_formatted.append(f"[{timestamp}] {text}")

        interactions_formatted = []
        for partner, msgs in interactions.items():
            if partner == "self":
                continue
            if msgs:
                interactions_formatted.append(f"\n--- Взаимодействие с {partner} ---")
                for interaction in msgs[-5:]:
                    if interaction.get("type") == "interaction":
                        partner_msg = interaction.get("partner_message", {})
                        user_msg = interaction.get("user_message")
                        p_ts = partner_msg.get("timestamp")
                        if hasattr(p_ts, "strftime"):
                            p_time = p_ts.strftime("%Y-%m-%d %H:%M")
                        else:
                            p_time = str(p_ts)
                        interactions_formatted.append(
                            f"[{p_time}] {partner}: {partner_msg.get('text', '')}"
                        )
                        if user_msg:
                            u_ts = user_msg.get("timestamp")
                            if hasattr(u_ts, "strftime"):
                                u_time = u_ts.strftime("%Y-%m-%d %H:%M")
                            else:
                                u_time = str(u_ts)
                            interactions_formatted.append(
                                f"[{u_time}] {username}: {user_msg.get('text', '')}"
                            )

        prompt = Config.PERSONAL_ANALYSIS_USER_PROMPT_TEMPLATE.format(
            username=username,
            user_messages="\n".join(user_msgs_formatted),
            interactions="\n".join(interactions_formatted)
        )
        return prompt

    def _format_analysis_report(self, analysis: Dict[str, Any], message_count: int) -> str:
        """Форматирует JSON-ответ AI в читаемый отчёт."""
        report = (
            f"📊 *Анализ коммуникаций*\n\n"
            f"📝 Проанализировано сообщений: {message_count}\n\n"
            f"🎯 *Тон общения:* {analysis.get('communication_tone', 'Не определён')}\n\n"
            f"📈 *Эффективность:* {analysis.get('effectiveness_score', 'N/A')}/10\n\n"
            f"🌍 *Атмосфера в команде:* {analysis.get('team_atmosphere', 'Не определена')}\n"
        )

        positive = analysis.get("positive_patterns", [])
        if positive:
            report += "\n✅ *Позитивные паттерны:*"
            for p in positive:
                report += f"\n• {p}"

        improvements = analysis.get("improvement_areas", [])
        if improvements:
            report += "\n\n⚠️ *Области для улучшения:*"
            for i in improvements:
                report += f"\n• {i}"

        recs = analysis.get("recommendations", [])
        if recs:
            report += "\n\n💡 *Рекомендации:*"
            for r in recs:
                report += f"\n• {r}"

        report += f"\n\n---\n📅 Анализ выполнен: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        return report

    def _format_personal_analysis_report(self, analysis: Dict[str, Any],
                                          username: str,
                                          message_count: int) -> str:
        report = (
            f"👤 *Персональный анализ для @{username}*\n\n"
            f"📊 Проанализировано {message_count} сообщений\n\n"
            f"🧭 *Общий вывод:*\n"
            f"{analysis.get('overall_summary', 'Не определен')}\n\n"
            f"📈 *Эффективность коммуникации:* {analysis.get('communication_effectiveness', 'N/A')}/10\n"
        )

        strengths = analysis.get("strengths", [])
        if strengths:
            report += "\n✅ *Сильные стороны:*"
            for s in strengths:
                report += f"\n• {s}"

        motivating = analysis.get("motivating_feedback", [])
        if motivating:
            report += "\n\n🌟 *Мотивирующая обратная связь:*"
            for item in motivating:
                quote = item.get("quote")
                ctx = item.get("context")
                result = item.get("positive_result")
                line = "• "
                if quote:
                    line += f"«{quote}»"
                if ctx:
                    line += f" — контекст: {ctx}"
                if result:
                    line += f" — результат: {result}"
                report += f"\n{line}"

        development = analysis.get("development_feedback", [])
        if development:
            report += "\n\n🛠️ *Зоны для развития:*"
            for item in development:
                quote = item.get("quote")
                action = item.get("action")
                cons = item.get("potential_consequences")
                question = item.get("reflection_question")
                suggestion = item.get("improvement_suggestion")

                if quote or action:
                    report += "\n• Ситуация:"
                    if quote:
                        report += f" «{quote}»"
                    if action:
                        report += f" | Действие: {action}"
                if cons:
                    report += f"\n  Последствия: {cons}"
                if question:
                    report += f"\n  Вопрос: {question}"
                if suggestion:
                    report += f"\n  Альтернатива: {suggestion}"

        interaction_patterns = analysis.get("interaction_patterns", {})
        if interaction_patterns:
            report += "\n\n🤝 *Особенности взаимодействия:*"
            for partner, pattern in interaction_patterns.items():
                report += f"\n• С {partner}: {pattern}"

        recs = analysis.get("recommendations", [])
        if recs:
            report += "\n\n💡 *Практические рекомендации:*"
            for rec in recs:
                report += f"\n• {rec}"

        agreements = analysis.get("agreements", [])
        if agreements:
            report += "\n\n📝 *Договоренности/следующие шаги:*"
            for agr in agreements:
                report += f"\n• {agr}"

        report += f"\n\n---\n📅 Анализ выполнен: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        report += "\n🔒 Этот отчет конфиденциален и отправлен только вам."

        return report
