from __future__ import annotations

def open_paper(signal, source="signal"):
    from paper_trading import open_from_signal
    return open_from_signal(signal, source=source)

def update_paper(notifier=None):
    from paper_trading import update_positions
    return update_positions(notifier=notifier)

def paper_performance():
    from paper_trading import performance
    return performance()
