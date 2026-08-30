import json
import sys

from research_collector import collect_project_research


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_research.py AAVE")
        return

    symbol = sys.argv[1]

    result = collect_project_research(symbol)

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()