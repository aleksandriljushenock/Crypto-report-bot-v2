# V40.1 — Global Quality hard floor

Исправлен приоритет порогов Quality/EV в AI Hedge gate.

Раньше профильные переменные вида `HEDGE_BREAKOUT_MIN_QUALITY=72` могли ослабить глобальный runtime-порог `HEDGE_MIN_QUALITY=75`, установленный из Telegram. Поэтому сигнал BREAKOUT с Quality 73 мог пройти.

Теперь итоговый порог вычисляется как максимум из глобального и профильного значений:

- effective Quality = max(HEDGE_MIN_QUALITY, HEDGE_<PROFILE>_MIN_QUALITY)
- effective EV = max(HEDGE_MIN_EV_PCT, HEDGE_<PROFILE>_MIN_EV_PCT)

Профиль может сделать фильтр строже, но не может ослабить глобальный минимум администратора.

Добавлены regression tests для обоих сценариев.
