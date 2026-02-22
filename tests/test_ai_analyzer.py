import json
from datetime import datetime

import pytest

from ai_analyzer import CommunicationAnalyzer


class DummyChoices:
    def __init__(self, content: str):
        class M:
            def __init__(self, c):
                self.content = c
        self.message = M(content)


class DummyResponse:
    def __init__(self, content: str):
        self.choices = [DummyChoices(content)]


class DummyChatCompletions:
    def __init__(self, payload):
        self.payload = payload

    async def create(self, model, messages, response_format, temperature, max_tokens):
        return DummyResponse(self.payload)


class DummyClient:
    def __init__(self, payload):
        class C:
            def __init__(self, p):
                self.completions = DummyChatCompletions(p)
        class Chat:
            def __init__(self, p):
                self.chat = self
                self.completions = DummyChatCompletions(p)
        # The real client is accessed as client.chat.completions.create
        self.chat = type("Chat", (), {"completions": DummyChatCompletions(payload)})()


@pytest.mark.asyncio
async def test_analyze_messages_formats_output(monkeypatch):
    analyzer = CommunicationAnalyzer()

    payload = json.dumps({
        "communication_tone": "Позитивный",
        "effectiveness_score": 8,
        "positive_patterns": ["взаимопомощь"],
        "improvement_areas": ["чаще подводить итоги"],
        "recommendations": ["ввести дневные дайджесты"],
        "team_atmosphere": "дружелюбная"
    })

    monkeypatch.setattr(analyzer, "client", DummyClient(payload))

    messages = [
        {"username": "u1", "text": "hello", "timestamp": datetime(2024,1,1,12,0,0)},
        {"username": "u2", "text": "hi", "timestamp": datetime(2024,1,1,12,1,0)},
    ]

    result = await analyzer.analyze_messages(messages)
    assert "Анализ 2 сообщений" in result
    assert "Позитивный" in result
    assert "8/10" in result


@pytest.mark.asyncio
async def test_analyze_user_communication_handles_empty(monkeypatch):
    analyzer = CommunicationAnalyzer()
    result = await analyzer.analyze_user_communication([], {}, "user")
    assert "Нет сообщений пользователя" in result


@pytest.mark.asyncio
async def test_analyze_user_communication_formats_output(monkeypatch):
    analyzer = CommunicationAnalyzer()
    payload = json.dumps({
        "overall_summary": "кратко",
        "communication_effectiveness": 7,
        "motivating_feedback": [],
        "development_feedback": [],
        "strengths": ["ясность"],
        "growth_areas": ["структурирование"],
        "interaction_patterns": {"bob": "вежливо"},
        "recommendations": ["делать выводы"],
        "agreements": ["созвон раз в неделю"]
    })
    monkeypatch.setattr(analyzer, "client", DummyClient(payload))

    messages = [
        {"username": "user", "text": "a", "timestamp": datetime(2024,1,1,10,0,0)}
    ]
    interactions = {"self": messages}
    result = await analyzer.analyze_user_communication(messages, interactions, "user")
    assert "Персональный анализ" in result
    assert "7/10" in result

