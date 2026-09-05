# v58.6.2 — VPS MAX / Autonomous Training Hardening

Целевая машина: 4 vCPU, 7.71 GiB RAM.

## Исправлено

- Исправлен legacy memory guard 340/470 MB, из-за которого сервисы ошибочно считали ~768 MB RSS критическим состоянием на 8-GB VPS.
- Memory guard теперь учитывает одновременно RSS процесса, MemAvailable хоста и cgroup/container limit/current usage.
- Добавлена автоматическая миграция старых Render-era лимитов: старый `.env` не требует ручного редактирования после обновления.
- Docker-контейнеру выделены 4 CPU и hard ceiling 6 GiB RAM; хосту остаётся безопасный запас.
- Execution ML вынесен в отдельный subprocess: sklearn/NumPy память полностью возвращается ОС после завершения обучения.
- Автоматический цикл остаётся `backfill -> train -> diagnose -> cloud verify`; ручной train/backfill/diagnose не нужен.
- Первый autonomous cycle запускается через 120 секунд после старта.
- Добавлены timeout, retry/backoff и persistent state для autonomous execution training.
- Отдельный periodic execution-backfill автоматически отключается, когда включён autonomous pipeline, чтобы не дублировать тяжёлую работу.
- Если тяжёлая задача временно пропущена из-за memory/busy guard, scheduler повторяет её через 60 секунд вместо ожидания полного многочасового интервала.
- Исправлен Chronos memory guard: он больше не читает напрямую старое значение `MEMORY_HARD_LIMIT_MB=470`, а использует фактический динамический memory profile.
- Увеличена вычислительная ёмкость execution training: 4 workers, 600 trees, 800 HGB iterations, до 30k rows, окна 1000/2500/5000/7500.
- Увеличена статистическая точность: 1200 block-bootstrap repetitions, regime model до 500 iterations, positive inner-CI gate включён.
- Увеличены общие learning/profile capacities: 20k cloud rows, 2400 search iterations, 20k profile rows.
- Cloud publish получил retry и read-after-write hash/schema/version verification; restore сначала валидирует joblib bundle во временном файле.
- Autonomous Telegram report теперь содержит краткий BREAKOUT analysis: AUC/PF/WF trades/PF/expectancy/reason.
- Исправлен release packaging: предыдущий builder не включал `Dockerfile.vps`; v58.6.2 включает его в релиз.
- Release builder по-прежнему fail-closed исключает `.env`, logs, runtime databases/models/caches и вложенные ZIP.
- Добавлены regression tests для VPS memory autoscale, cgroup reserve, Docker limits, isolated worker и VPS Dockerfile packaging.

## Финальный ресурсный профиль

- VPS: 4 vCPU / 7.71 GiB RAM.
- Docker CPU ceiling: 4.0 CPU.
- Docker memory ceiling: 6 GiB.
- Docker memory reservation: 2 GiB.
- App memory soft RSS: 4600 MB.
- App memory hard RSS: 5600 MB.
- Minimum available soft reserve: 1800 MB.
- Minimum available hard reserve: 1100 MB.
- Execution training workers: 4.
- OMP threads: 4.
- MKL threads: 1.
- OpenBLAS threads: 1.
- NumExpr threads: 2.
- Chronos Torch threads: 3.
- Chronos required memory headroom: 512 MB.
- Execution max rows: 30000.
- Execution trees: 600.
- Execution HGB iterations: 800.
- Regime iterations: 500.
- Block-bootstrap reps: 1200.
- Training timeout: 10800 sec (3 hours).
- Automatic retries: 2, base backoff 60 sec.
- Initial autonomous training delay: 120 sec.

## Эксплуатация

После `git pull` + rebuild/restart никаких ручных команд обучения не требуется. Сервис сам обновляет dataset, обучает, валидирует, сохраняет diagnostic и публикует проверенный runtime bundle.
