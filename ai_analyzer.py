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
        # Настраиваем Gemini с вашим ключом
        genai.configure(api_key=Config.GEMINI_API_KEY)
        
        # Устанавливаем модель
        self.model_name = "models/gemini-2.5-flash" 
        
        # Диагностика: проверяем доступные модели при инициализации (СИНХРОННО, без await)
        try:
            print("🔍 Проверяю доступные модели Google Gemini...")
            models = genai.list_models()  # это синхронный метод, не требует await
            available_models = []
            for m in models:
                if 'generateContent' in m.supported_generation_methods:
                    available_models.append(m.name)
            
            print(f"✅ Доступные модели для generateContent: {available_models}")
            
            # Проверяем, что выбранная модель есть в списке
            if self.model_name not in available_models:
                print(f"⚠️ Внимание: модель {self.model_name} не найдена в списке доступных!")
                if available_models:
                    # Предлагаем первую доступную модель
                    suggested = available_models[0]
                    print(f"💡 Рекомендуется использовать: {suggested}")
                    print(f"   Замените self.model_name на '{suggested}' в __init__")
            else:
                print(f"✅ Модель {self.model_name} доступна и будет использоваться")
                
        except Exception as e:
            print(f"❌ Не удалось получить список моделей: {e}")
            print("💡 Проверьте интернет-соединение и правильность API-ключа") 

    async def _call_gemini(self, system_prompt: str, user_prompt: str, 
                           temperature: float = 0.4, max_tokens: int = 3000, 
                           response_json: bool = True) -> str:
        """
        Вспомогательный метод для вызова Gemini API асинхронно.
        """
        try:
            # Импортируем правильные классы
            from google.generativeai.types import HarmCategory, HarmBlockThreshold
            
            # Настройки безопасности - ПОЛНОСТЬЮ ОТКЛЮЧАЕМ ВСЕ ФИЛЬТРЫ
            safety_settings = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }
            
            # Создаём модель с системным промптом и настройками безопасности
            model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=system_prompt,
                safety_settings=safety_settings
            )

            # Настройки генерации
            generation_config = {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
            if response_json:
                generation_config["response_mime_type"] = "application/json"

            # Запускаем синхронный вызов в отдельном потоке
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: model.generate_content(
                    user_prompt,
                    generation_config=generation_config,
                )
            )

            # Проверяем, не заблокирован ли ответ
            if response.prompt_feedback and response.prompt_feedback.block_reason:
                block_reason = response.prompt_feedback.block_reason
                logger.warning(f"Запрос был заблокирован по причине: {block_reason}")
                # Возвращаем понятное сообщение вместо ошибки
                return f"⚠️ Запрос был заблокирован API по причине: {block_reason}. Попробуйте смягчить формулировки."

            return response.text
            
        except Exception as e:
            logger.error(f"Gemini API call failed: {e}")
            # Добавим более подробное логирование
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                logger.error(f"Response text: {e.response.text}")
            
            # Возвращаем понятное сообщение пользователю вместо выбрасывания исключения
            error_message = str(e)
            if "404" in error_message and "model" in error_message:
                return "❌ Модель не найдена. Проверьте правильность имени модели в настройках."
            elif "API key" in error_message:
                return "❌ Проблема с API ключом. Проверьте правильность ключа в .env файле."
            elif "quota" in error_message.lower() or "rate limit" in error_message.lower():
                return "❌ Превышен лимит запросов к API. Подождите немного и попробуйте снова."
            else:
                return f"❌ Ошибка при обращении к AI: {error_message[:100]}..." 

    async def check_available_models(self):
        """Проверить, какие модели доступны для generateContent"""
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
        """
        Анализ групповых сообщений, возвращает отчёт.
        """
        if not messages:
            return "\u274c Нет сообщений для анализа."

        try:
            formatted_messages = self._format_messages(messages)
            analysis_prompt = self._create_analysis_prompt(
                formatted_messages, len(messages))

            system_prompt = self._get_system_prompt()
            response_content = await self._call_gemini(
                system_prompt=system_prompt,
                user_prompt=analysis_prompt,
                temperature=0.4,
                max_tokens=3000,
                response_json=True
            )

            if not response_content:
                return "\u274c Получен пустой ответ от AI."

            analysis_json = json.loads(response_content)
            return self._format_analysis_report(analysis_json, len(messages))

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {e}")
            return "\u274c Ошибка обработки ответа AI. Попробуйте позже."
        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return f"\u274c Ошибка при анализе: {str(e)}"

    async def analyze_user_communication(
        self,
        user_messages: List[Dict[str, Any]],
        interactions: Dict[str, List[Dict[str, Any]]],
        username: str,
    ) -> str:
        """
        Персональный анализ для конкретного пользователя.
        """
        if not user_messages:
            return "\u274c Нет сообщений пользователя для анализа."

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
                return "\u274c Получен пустой ответ от AI."

            analysis_json = json.loads(response_content)
            return self._format_personal_analysis_report(
                analysis_json, username, len(user_messages))

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {e}")
            return "\u274c Ошибка обработки ответа AI. Попробуйте позже."
        except Exception as e:
            logger.error(f"Personal analysis failed: {e}")
            return f"\u274c Ошибка при анализе: {str(e)}"

    def _format_messages(self, messages: List[Dict[str, Any]]) -> str:
        formatted = []
        for msg in messages:
            ts = msg.get("timestamp")
            if hasattr(ts, "strftime"):
                timestamp = ts.strftime("%Y-%m-%d %H:%M")
            else:
                timestamp = str(ts)
            username = msg.get("username", "unknown")
            text = msg.get("text", "")
            formatted.append(f"[{timestamp}] {username}: {text}")
        return "\n".join(formatted)

    def _get_system_prompt(self) -> str:
        return Config.GROUP_ANALYSIS_SYSTEM_PROMPT

    def _create_analysis_prompt(self, formatted_messages: str,
                                message_count: int) -> str:
        return Config.GROUP_ANALYSIS_USER_PROMPT_TEMPLATE.format(
            message_count=message_count,
            formatted_messages=formatted_messages
        )

    def _format_analysis_report(self, analysis: Dict[str, Any],
                                message_count: int) -> str:
        report = (
            f"\U0001f4ca **Анализ {message_count} сообщений**\n"
            f"\U0001f3af **Общий тон коммуникации:**\n"
            f"{analysis.get('communication_tone', 'Не определен')}\n"
            f"\U0001f4c8 **Оценка эффективности:** {analysis.get('effectiveness_score', 'N/A')}/10\n"
            f"\u2705 **Позитивные паттерны:**"
        )
        for pattern in analysis.get("positive_patterns", []):
            report += f"\n-  {pattern}"
        report += "\n\n\U0001f527 **Области для улучшения:**"
        for area in analysis.get("improvement_areas", []):
            report += f"\n-  {area}"
        report += "\n\n\U0001f4a1 **Рекомендации:**"
        for rec in analysis.get("recommendations", []):
            report += f"\n-  {rec}"
        report += f"\n\n\U0001f31f **Атмосфера в команде:**\n{analysis.get('team_atmosphere', 'Не определена')}"
        report += f"\n\n---\n\U0001f4c5 Анализ выполнен: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        return report

        

    async def analyze_conflict(self, messages: List[Dict[str, Any]]) -> str:
        """
        Анализ конфликта в диалоге.
        """
        if not messages:
            return "\u274c Нет сообщений для анализа."

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
            return f"\u274c Ошибка при анализе конфликта: {str(e)}"

    async def analyze_tips(self, messages: List[Dict[str, Any]]) -> str:
        """
        Выделение полезных советов, лайфхаков, цитат из диалога.
        """
        if not messages:
            return "\u274c Нет сообщений для анализа."

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
            return f"\u274c Ошибка при выделении советов: {str(e)}"

    def _get_personal_analysis_system_prompt(self) -> str:
        return Config.PERSONAL_ANALYSIS_SYSTEM_PROMPT

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
                interactions_formatted.append(
                    f"\n--- Взаимодействие с {partner} ---")
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
            user_messages=chr(10).join(user_msgs_formatted),
            interactions=chr(10).join(interactions_formatted)
        )
        return prompt

    def _format_personal_analysis_report(self, analysis: Dict[str, Any],
                                         username: str,
                                         message_count: int) -> str:
        report = (
            f"\U0001f464 **Персональный анализ для @{username}**\n\n"
            f"\U0001f4ca Проанализировано {message_count} сообщений\n\n"
            f"\U0001f9ed **Общий вывод:**\n"
            f"{analysis.get('overall_summary', 'Не определен')}\n\n"
            f"\U0001f4c8 **Эффективность коммуникации:** {analysis.get('communication_effectiveness', 'N/A')}/10\n"
        )

        strengths = analysis.get("strengths", [])
        if strengths:
            report += "\n\u2705 **Сильные стороны:**"
            for s in strengths:
                report += f"\n-  {s}"

        motivating = analysis.get("motivating_feedback", [])
        if motivating:
            report += "\n\n\U0001f31f **Мотивирующая ОС (что стоит закрепить):**"
            for item in motivating:
                quote = item.get("quote")
                ctx = item.get("context")
                result = item.get("positive_result")
                line = "-  "
                if quote:
                    line += f"\u00ab{quote}\u00bb"
                if ctx:
                    line += f" \u2014 контекст: {ctx}"
                if result:
                    line += f" \u2014 результат: {result}"
                report += f"\n{line}"

        development = analysis.get("development_feedback", [])
        if development:
            report += "\n\n\U0001f6e0\ufe0f **Зоны для развития (корректирующая/развивающая ОС):**"
            for item in development:
                quote = item.get("quote")
                action = item.get("action")
                cons = item.get("potential_consequences")
                question = item.get("reflection_question")
                suggestion = item.get("improvement_suggestion")

                if quote or action:
                    report += "\n-  Ситуация:"
                    if quote:
                        report += f" \u00ab{quote}\u00bb"
                    if action:
                        report += f" | Действие: {action}"
                if cons:
                    report += f"\n  Последствия/риск: {cons}"
                if question:
                    report += f"\n  Вопрос для рефлексии: {question}"
                if suggestion:
                    report += f"\n  Альтернатива: {suggestion}"

        interaction_patterns = analysis.get("interaction_patterns", {})
        if interaction_patterns:
            report += "\n\n\U0001f91d **Особенности взаимодействия:**"
            for partner, pattern in interaction_patterns.items():
                report += f"\n-  С {partner}: {pattern}"

        recs = analysis.get("recommendations", [])
        if recs:
            report += "\n\n\U0001f4a1 **Практические рекомендации:**"
            for rec in recs:
                report += f"\n-  {rec}"

        agreements = analysis.get("agreements", [])
        if agreements:
            report += "\n\n\U0001f4dd **Договоренности/следующие шаги:**"
            for agr in agreements:
                report += f"\n-  {agr}"

        report += f"\n\n---\n\U0001f4c5 Персональный анализ выполнен: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        report += "\n\U0001f512 Этот отчет конфиденциален и отправлен только вам."

        return report