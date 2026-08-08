"""Manual CloudLearningStore smoke test; safe to collect under pytest."""


def main() -> None:
    from cloud_learning_store import CloudLearningStore

    rows = CloudLearningStore().unresolved(limit=5)
    print("Подключение работает")
    print("Количество pending-записей:", len(rows))


if __name__ == "__main__":
    main()
