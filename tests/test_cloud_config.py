"""Manual cloud configuration smoke test; no import-time validation."""


def main() -> None:
    from cloud_config import (
        ENV_FILE,
        SUPABASE_MODEL_BUCKET,
        SUPABASE_SERVICE_KEY,
        SUPABASE_URL,
        validate_cloud_config,
    )

    validate_cloud_config()
    print("Конфигурация загружена успешно")
    print("Файл .env:", ENV_FILE)
    print("SUPABASE_URL configured:", bool(SUPABASE_URL))
    print("Bucket:", SUPABASE_MODEL_BUCKET)
    print("Service key configured:", bool(SUPABASE_SERVICE_KEY))


if __name__ == "__main__":
    main()
