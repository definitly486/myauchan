# My Auchan для Android

Простое нативное Android-приложение на Kotlin, повторяющее основной сценарий `auchan_purchases.py`:

- вход по номеру телефона через Token20 → CheckPhone → SMS → Token80;
- сохранение Token80 в зашифрованном виде с помощью Android Keystore;
- постоянный DeviceId;
- загрузка истории покупок через `POST /ecom/lk/loyalty/history`;
- постраничная загрузка (серверный размер страницы — 10);
- отображение магазина, даты, суммы, номера чека и состава чека;
- удаление локальной сессии при HTTP 401.

Приложение не запрашивает и не сохраняет SMS-код. Токен не выводится в интерфейс или логи.

## Сборка

Откройте проект в Android Studio либо выполните:

```sh
gradle assembleDebug
```

APK: `app/build/outputs/apk/debug/app-debug.apk`.

## GitHub Actions

Workflow `.github/workflows/android.yml` собирает APK при изменениях ветки `app` и вручную через **Actions → Android APK → Run workflow**. Готовый APK доступен в Artifacts запуска workflow.

Минимальная версия Android — 6.0 (API 23), compile/target SDK — 35.
