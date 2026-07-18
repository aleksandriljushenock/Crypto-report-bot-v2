import json
import sys

from research_analyzer import (
    analyze_project_research,
)
from research_collector import (
    collect_project_research,
)


def main():
    if len(sys.argv) < 2:
        print(
            "Usage: "
            "python test_research_analysis.py AAVE"
        )
        return

    symbol = sys.argv[1]

    print(f"Collecting research for {symbol}...")

    raw_data = collect_project_research(
        symbol
    )

    analysis = analyze_project_research(
        raw_data
    )

    print(
        json.dumps(
            analysis,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()