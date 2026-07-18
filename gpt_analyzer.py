import os
import requests
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = """
Ты профессиональный криптотрейдер институционального уровня.
Твоя задача — не искать вход любой ценой, а отбирать только сделки с положительным математическим ожиданием.

Правила:
- Если сетап слабый — пиши НЕ ВХОДИТЬ.
- Если R/R ниже 1:2 — пиши НЕ ВХОДИТЬ.
- Не выдумывай данные.
- Используй только переданные метрики.
- Формат ответа должен быть кратким.
"""

USER_TEMPLATE = """
Проанализируй торговый сетап по данным:

{data}

Дай ответ в формате:

Монета:
Направление:
Уверенность:
Почему:
Что смущает:
Вход:
Стоп:
TP1:
TP2:
TP3:
Risk/Reward:
Что отменяет сценарий:
Итог:
"""


def analyze_with_gpt(compact_data: str) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3.1:free")

    if not api_key:
        return "GPT-анализ пропущен: OPENROUTER_API_KEY не найден."

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost",
                "X-OpenRouter-Title": "Crypto Report Service",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": USER_TEMPLATE.format(data=compact_data),
                    },
                ],
                "temperature": 0.2,
            },
            timeout=60,
        )

        if response.status_code != 200:
            return (
                "GPT-анализ пропущен: ошибка OpenRouter. "
                f"Status={response.status_code}. Body={response.text[:500]}"
            )

        data = response.json()
        return data["choices"][0]["message"]["content"]

    except Exception as exc:
        return f"GPT-анализ пропущен из-за ошибки OpenRouter: {str(exc)[:500]}"