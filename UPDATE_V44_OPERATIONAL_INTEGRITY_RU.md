# V44.0.0 Operational Integrity

Исправления V44 сфокусированы на fail-closed Paper/Cloud поведении, непрерывности свечной истории, корректной классификации направлений, устойчивости cloud-sync, уникальных версиях V14, retention outcome-истории и безопасном VPS startup.

## Ключевые изменения
- Paper cursor не перескакивает через пропуски исторических свечей.
- Ошибка загрузки Paper positions теперь fail-closed.
- Неизвестное направление outcome не считается SHORT.
- Horizon labels отделены от реальных execution TP/SL.
- Cloud update успешен только при реально изменённой строке; lookup errors fail-closed.
- Pending cloud observations обрабатываются от старых к новым.
- V14 versions используют microseconds.
- Старые completed tracked signals очищаются по retention.
- Docker VPS entrypoint исправляет права data/logs перед запуском bot user.
- Дубликаты ENV-ключей удалены.
