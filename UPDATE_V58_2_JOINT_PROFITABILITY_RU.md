# v58.2 Joint Profitability Engine

Цель: повысить точность отбора прибыльных BREAKOUT-сделок без ослабления OOS safety gates.

Изменения:
- Profitability selector теперь требует согласия двух независимых сигналов: прогноз net return + P(profit).
- Оба порога подбираются только на selection-сегменте; champion/OOS не используется для тюнинга.
- Walk-forward теперь в каждом фолде переобучает и return-модель, и classifier только на прошлом.
- Для classifier внутри каждого WF-фолда выделяется отдельный исторический calibration tail; test fold остаётся untouched.
- Добавлены worst-fold expectancy/PF и полные fold-метрики в диагностику.
- Fail-closed сохранён: отрицательный valid WF fold блокирует economic champion.
- GLOBAL/PULLBACK не получили обходного пути к economic champion.
- Старые AUC/Brier/Precision/PF/expectancy gates не снижены.

Почему: v58.1 показал сильный BREAKOUT HGB/2500 на selection/champion, но 1 из 3 walk-forward фолдов был отрицательным. v58.2 не скрывает этот факт усреднением, а добавляет classifier agreement для отсечения сделок, которые return-regressor выбирает без подтверждения вероятности прибыли.
