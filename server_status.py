from __future__ import annotations

import os
import shutil
from pathlib import Path


def _read_meminfo() -> tuple[float, float]:
    values: dict[str, int] = {}
    try:
        for line in Path('/proc/meminfo').read_text(encoding='utf-8').splitlines():
            key, raw = line.split(':', 1)
            values[key] = int(raw.strip().split()[0])
        total_kb = values.get('MemTotal', 0)
        available_kb = values.get('MemAvailable', 0)
        used_kb = max(0, total_kb - available_kb)
        return used_kb / 1024 / 1024, total_kb / 1024 / 1024
    except Exception:
        return 0.0, 0.0


def _uptime() -> str:
    try:
        seconds = float(Path('/proc/uptime').read_text().split()[0])
    except Exception:
        return 'n/a'
    days, seconds = divmod(int(seconds), 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        return f'{days}d {hours}h {minutes}m'
    return f'{hours}h {minutes}m'


def build_server_status() -> str:
    cpu_count = os.cpu_count() or 1
    try:
        load1, load5, load15 = os.getloadavg()
        cpu_load_pct = max(0.0, min(999.0, load1 / cpu_count * 100.0))
        load_text = f'{load1:.2f} / {load5:.2f} / {load15:.2f}'
    except Exception:
        cpu_load_pct = 0.0
        load_text = 'n/a'

    used_gb, total_gb = _read_meminfo()
    disk = shutil.disk_usage('/')
    disk_used = (disk.total - disk.free) / 1024**3
    disk_total = disk.total / 1024**3
    disk_pct = (disk.total - disk.free) / disk.total * 100 if disk.total else 0

    providers = os.getenv('TRADE_MARKET_PROVIDERS', 'binance,bybit')
    chronos = os.getenv('CHRONOS_ENABLED', 'false').lower() in {'1','true','yes','on'}
    paper = os.getenv('PAPER_TRADING_ENABLED', 'true').lower() in {'1','true','yes','on'}

    return '\n'.join([
        '🖥 <b>СЕРВЕР</b>',
        '',
        f'✅ Container: <b>ONLINE</b>',
        f'⏱ Host uptime: <b>{_uptime()}</b>',
        f'🧮 CPU: <b>{cpu_count}</b> cores • load ≈ <b>{cpu_load_pct:.0f}%</b>',
        f'   load avg: <code>{load_text}</code>',
        f'🧠 RAM: <b>{used_gb:.2f} / {total_gb:.2f} GB</b>',
        f'💽 Disk: <b>{disk_used:.1f} / {disk_total:.1f} GB</b> ({disk_pct:.0f}%)',
        '',
        f'🏦 Providers: <code>{providers}</code>',
        f'🧪 Paper Trading: <b>{"ON" if paper else "OFF"}</b>',
        f'🧠 Chronos default: <b>{"ON" if chronos else "OFF"}</b>',
        '',
        'ℹ️ Точные биржевые health-checks: 📊 Аналитика → 🏦 Биржи',
    ])
