from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategySpec:
    key: str
    title: str
    emoji: str
    short: str
    description: str
    rules: tuple[str, ...]
    needs_derivatives: bool = False
    needs_h1: bool = False


STRATEGIES: tuple[StrategySpec, ...] = (
    StrategySpec(
        "fib_05_pullback", "Fib 0.5 Pullback", "🟦", "fib05",
        "D1 тренд + значимый swing + support около Fib 0.5 + H4 подтверждение.",
        (
            "Ликвидный USDT perpetual.",
            "На D1 нужен восходящий значимый swing и тренд.",
            "Fib 0.5 должен совпадать с самостоятельной support-zone.",
            "На H4 нужен bullish trigger около зоны.",
            "SL ниже support с ATR buffer, TP перед D1 swing high.",
        ),
    ),
    StrategySpec(
        "ma_ribbon_cross", "MA Ribbon 8/13/21/55", "📶", "maribbon",
        "H4 bullish MA-ribbon reversal: EMA8 + SMA13/SMA21 подтверждённо переходят выше SMA55 на ликвидных монетах.",
        (
            "Universe: USDT perpetual с 24h quote volume от $100M.",
            "Рабочий таймфрейм H4; D1 используется как фильтр старшего тренда.",
            "EMA8 и SMA13/SMA21 должны перейти из-под SMA55 выше неё в небольшом временном окне.",
            "Для BUY нужен чистый bullish stack: price > EMA8 > SMA13 > SMA21 > SMA55.",
            "SMA55 должна иметь положительный slope; D1 не должен быть медвежьим, READY требует D1 UP.",
            "Дополнительные фильтры качества: volume expansion, RSI без перекупленности и H4 structure confirmation.",
            "Entry — stop-confirmation выше сигнальной H4 свечи; SL ниже локальной структуры/MA55 с ATR buffer; TP около 2.5R или D1 high.",
            "Важно: буквальный рост SMA55 сквозь EMA8/SMA13/SMA21 обычно является медвежьим событием; он отслеживается, но не создаёт BUY.",
        ),
    ),
    StrategySpec(
        "ma55_cycle", "MA55 Cycle 8/13/21/55", "🔄", "ma55cycle",
        "Циклическая LONG-стратегия: BUY, когда SMA55 проходит EMA8/SMA13/SMA21 сверху вниз; выход — обратное пересечение снизу вверх.",
        (
            "Universe: USDT perpetual с 24h quote volume от $100M.",
            "Рабочий таймфрейм H4; используются EMA8 и SMA13/SMA21/SMA55.",
            "BUY-event: SMA55 завершает переход сверху вниз через EMA8/SMA13/SMA21; сам переход может занимать до 12 H4 свечей.",
            "После завершения cross окно подтверждения живёт максимум 3 H4 свечи (~12ч), но BUY разрешён сразу — ждать 12 часов не нужно.",
            "D1 DOWN блокирует BUY; D1 RANGE разрешён. Для READY нужен чистый stack price > EMA8 > SMA13 > SMA21 > SMA55 и минимум 2 из 4 подтверждений: D1 UP/improving, SMA55 slope >= 0, RSI 48–76, volume >= 0.90 avg20.",
            "Цена не должна быть дальше 2 ATR от EMA8; иначе setup остаётся WATCH/протухает как поздний вход.",
            "После READY forward-entry фиксируется по первой будущей 1H свече, чтобы не использовать цену задним числом.",
            "Обычный EXIT: SMA55 проходит EMA8/SMA13/SMA21 снизу вверх. На этом событии отправляется сигнал закрыть LONG.",
            "Защитный аварийный SL остаётся под H4 structure/MA55: он нужен только для ограничения риска, если рынок обвалится раньше обратного MA-cross.",
            "Статистика считает фактический return от forward-entry до reverse-cross/SL, Win Rate, Profit Factor, expectancy, cumulative return и max drawdown.",
        ),
    ),
    StrategySpec(
        "smart_money_confluence", "Smart Money Confluence", "🐋", "smc",
        "SMC confluence: liquidity sweep + market structure shift + Order Block/FVG + premium/discount context.",
        (
            "На D1/H4 определяется рыночный bias и внешняя ликвидность.",
            "Нужен liquidity sweep/reclaim значимого H4 swing либо цена должна находиться рядом с активной liquidity-zone.",
            "На H1 ищется market structure shift (BOS/CHoCH/rejection) в сторону сделки.",
            "Order Block или FVG должны подтверждать направление; лучший setup — их overlap/confluence.",
            "LONG предпочтителен в discount, SHORT — в premium части H4 range.",
            "Funding/OI используются только как дополнительный голос, отсутствие данных не превращается в zero.",
            "Entry после подтверждения, SL за sweep/extreme, TP к противоположной внешней ликвидности; минимум около 2R.",
        ),
        needs_derivatives=True,
        needs_h1=True,
    ),
    StrategySpec(
        "liquidity_sweep_reclaim", "Liquidity Sweep + Reclaim", "🟪", "sweep",
        "Снятие ликвидности за H4 swing с возвратом в диапазон и подтверждением структуры.",
        (
            "На H4 определяется подтверждённый swing high/low.",
            "Цена должна вынести уровень и закрыться обратно внутри диапазона.",
            "Для READY требуется reclaim + направленный structure break/confirmation.",
            "Entry после подтверждения, SL за sweep-extreme.",
            "TP к противоположной ликвидности или минимум около 2R.",
        ),
    ),
    StrategySpec(
        "ema_trend_pullback", "EMA Trend Pullback", "🟧", "ema",
        "D1 тренд и H4 откат к EMA20/EMA50 с подтверждением продолжения.",
        (
            "D1 trend filter по EMA50/EMA200.",
            "H4 цена возвращается к EMA20/EMA50.",
            "Нужен rejection/BOS/engulfing в сторону D1 тренда.",
            "Entry в динамической EMA-zone, SL за H4 structure.",
            "TP к последнему D1/H4 экстремуму или по R/R.",
        ),
    ),
    StrategySpec(
        "breakout_retest", "Breakout → Retest", "🟩", "retest",
        "Пробой H4/D1 уровня, возврат к нему и подтверждение удержания после retest.",
        (
            "Ищется устойчивый H4 support/resistance.",
            "Нужен breakout закрытием, а не только wick.",
            "После breakout ждём retest бывшего уровня.",
            "READY только после rejection/continuation confirmation.",
            "SL за retest-zone, TP по следующему swing/минимум 2R.",
        ),
    ),
    StrategySpec(
        "range_mean_reversion", "Range Mean Reversion", "🟨", "range",
        "Возврат к среднему внутри устойчивого H4 диапазона без выраженного D1 тренда.",
        (
            "H4 должен находиться в устойчивом диапазоне с несколькими касаниями границ.",
            "Сильный тренд исключает setup.",
            "LONG ищется у нижней границы, SHORT у верхней.",
            "Нужен rejection candle/возврат внутрь диапазона.",
            "TP ближе к середине/противоположной части range, SL вне диапазона.",
        ),
    ),
    StrategySpec(
        "anchored_vwap_pullback", "Anchored VWAP Pullback", "🟫", "avwap",
        "Откат в тренде к Anchored VWAP, построенному от значимого H4 swing.",
        (
            "D1 определяет направление основного тренда.",
            "AVWAP якорится от значимого H4 swing/extreme.",
            "Цена должна вернуться к AVWAP без разрушения структуры.",
            "Нужен H4 rejection/continuation trigger.",
            "SL за локальный swing, TP к trend extreme или минимум около 2R.",
        ),
    ),
    StrategySpec(
        "volatility_squeeze", "Volatility Squeeze", "🟦", "squeeze",
        "Сжатие H4 volatility/Bollinger width с последующим подтверждённым expansion breakout.",
        (
            "Bollinger width должен находиться около нижних значений своей истории.",
            "Объём во время breakout должен расширяться.",
            "READY после закрытия за squeeze-range в сторону breakout.",
            "Entry stop-order за breakout candle, SL внутри/за противоположной границей.",
            "TP фиксируется по R/R, потому что заранее swing high может отсутствовать.",
        ),
    ),
    StrategySpec(
        "donchian_trend", "Donchian Trend Following", "🟢", "donchian",
        "Трендовая торговля пробоя 20-периодного H4 Donchian channel по D1 trend filter.",
        (
            "D1 задаёт направление тренда.",
            "H4 строит Donchian high/low по предыдущим 20 свечам.",
            "READY при подтверждённом закрытии за каналом по тренду.",
            "Entry stop-order за breakout level.",
            "SL за коротким противоположным channel/swing, TP около 3R.",
        ),
    ),
    StrategySpec(
        "funding_oi_squeeze", "Funding + OI Squeeze", "🟥", "fundingoi",
        "Экстремальный funding + рост OI + ценовой trigger для потенциального squeeze.",
        (
            "Используются derivatives данные доступной биржи через capability layer.",
            "LONG: отрицательный funding и растущий OI; SHORT зеркально.",
            "Цена должна подтвердить squeeze H4 structure breakout.",
            "Отсутствующие funding/OI не превращаются в нули — setup пропускается.",
            "SL за H4 structure, TP по 2.5R или ближайшей ликвидности.",
        ),
        needs_derivatives=True,
    ),
    StrategySpec(
        "oi_price_divergence", "OI / Price Divergence", "🔵", "oidiv",
        "Расхождение экстремума цены и динамики Open Interest с reversal подтверждением H4.",
        (
            "Нужна валидная история OI, а не synthetic zero.",
            "Новый price high при слабом/падающем OI ищет SHORT exhaustion; low — LONG.",
            "Одного divergence недостаточно: нужен H4 reversal candle/structure break.",
            "Entry после confirmation, SL за ценовой extreme.",
            "TP к средней/противоположной части локального диапазона.",
        ),
        needs_derivatives=True,
    ),
    StrategySpec(
        "rsi_divergence_structure", "RSI Divergence + Structure", "🟣", "rsidiv",
        "H4 RSI divergence только вместе со swing-структурой и reversal confirmation.",
        (
            "На H4 сравниваются два последних подтверждённых price pivots.",
            "Bullish: lower low цены + higher low RSI; bearish зеркально.",
            "Divergence без structure confirmation не является READY.",
            "Entry после reclaim/BOS, SL за второй pivot.",
            "TP к локальной противоположной liquidity-zone или минимум 2R.",
        ),
    ),
)

BY_KEY = {x.key: x for x in STRATEGIES}
BY_SHORT = {x.short: x for x in STRATEGIES}


def get_strategy(key_or_short: str) -> StrategySpec:
    key_or_short = str(key_or_short or "").strip().lower()
    if key_or_short in BY_KEY:
        return BY_KEY[key_or_short]
    if key_or_short in BY_SHORT:
        return BY_SHORT[key_or_short]
    raise KeyError(key_or_short)
