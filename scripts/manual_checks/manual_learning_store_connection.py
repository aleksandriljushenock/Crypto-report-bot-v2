from cloud_learning_store import CloudLearningStore


def main() -> None:
    store = CloudLearningStore()

    rows = store.unresolved(limit=5)

    print("Подключение работает")
    print("Количество pending-записей:", len(rows))
    print("Записи:", rows)


if __name__ == "__main__":
    main()