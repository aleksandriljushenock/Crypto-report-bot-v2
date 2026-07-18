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
print("SUPABASE_URL:", SUPABASE_URL)
print("Bucket:", SUPABASE_MODEL_BUCKET)
print("Ключ найден:", bool(SUPABASE_SERVICE_KEY))
print("Длина ключа:", len(SUPABASE_SERVICE_KEY))