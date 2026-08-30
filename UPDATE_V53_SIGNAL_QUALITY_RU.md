# V53 — Signal Quality Architecture

V53 перестраивает прогнозирование вокруг качества реального исполнения, а не вокруг красивых historical score.

Ключевые изменения:
- execution-first Profit Profile schema 53; legacy v2/v43 профили fail-closed;
- Paper outcomes имеют приоритет над 24h mark-to-market labels;
- leverage-independent execution target: net PnL / notional и R-multiple;
- отдельные setup/direction contexts и specialists для PULLBACK/BREAKOUT, LONG/SHORT;
- BREAKOUT автоматически уходит в shadow при отрицательной specialist expectancy;
- historical probability и historical expectancy разделены;
- Quality больше не включает EV второй раз, чтобы не удваивать Probability;
- dynamic rule decay отключает положительный rule bonus, если recent expectancy стала отрицательной;
- Profile health guard использует recent AUC/Brier/expectancy и ужесточает thresholds при деградации;
- Reliability Score учитывает missing/fallback features и coverage бирж;
- probability interval 95% показывает uncertainty;
- финальный execution gate повторно проверяет Reliability, degraded thresholds и V53 engine decision после Chronos/Adaptive;
- SHORT entry/SL/TP geometry полностью зеркальна LONG;
- confirmations разделены по направлению; bullish confirmations больше не усиливают SHORT;
- OI bug signal/label исправлен; отсутствие OI теперь neutral evidence, а не фиктивные 70 как полезный фактор;
- learning v14 target schema обновлён до execution_v53;
- добавлены setup specialists и setup calibration;
- Walk Forward по умолчанию усилен, random search рассчитан на более мощный сервер;
- Adaptive Model v53 получил direction/setup/regime/reliability/uncertainty features и остаётся gated до достаточного Paper ledger.

Bundled profile пересобран на доступной истории пользователя: 1608 наблюдений, из них 35 matched Paper executions. На момент сборки execution win rate остаётся низким, поэтому V53 намеренно работает консервативно и не считает старые высокие Probability доказательством edge.
