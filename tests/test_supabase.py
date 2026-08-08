"""Manual Supabase smoke test; safe to collect under pytest."""


def main() -> None:
    from cloud_client import get_supabase_client

    supabase = get_supabase_client()
    response = supabase.table("learning_observations").select("*").limit(1).execute()
    print("Подключение к Supabase успешно")
    print("Получено строк:", len(response.data or []))


if __name__ == "__main__":
    main()
