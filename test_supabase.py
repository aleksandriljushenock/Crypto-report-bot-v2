from cloud_client import get_supabase_client


def main() -> None:
    supabase = get_supabase_client()

    response = (
        supabase
        .table("learning_observations")
        .select("*")
        .limit(1)
        .execute()
    )

    print("Подключение к Supabase успешно")
    print("Полученные данные:", response.data)


if __name__ == "__main__":
    main()