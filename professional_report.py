from capital_flow_engine import build_capital_flow_report
from smart_money_engine import build_smart_money_report
from narrative_engine import build_narrative_report
from sentiment_engine import build_sentiment_report
from portfolio_manager import portfolio_report
from self_learning_engine import build_learning_report
from ai_intelligence import build_top_ai_report

def build_professional_report():
    sections=[]
    for fn in [lambda: build_top_ai_report(refresh=False),build_capital_flow_report,build_sentiment_report,build_narrative_report,build_smart_money_report,build_learning_report,portfolio_report]:
        try:sections.append(fn())
        except Exception as e:sections.append(f'<b>Ошибка модуля</b>\n<code>{str(e)[:300]}</code>')
    return '\n\n━━━━━━━━━━━━\n\n'.join(sections)
