#!/usr/bin/env python3
"""
Auchan purchase history client.

Based on the My Auchan 4.1.2 API declarations in:
https://github.com/definitly486/AuchanReverseEngineering

Required environment:
  AUCHAN_TOKEN20_PASSWORD  - Token20 password/header value used by the app
  AUCHAN_DEVICE_ID         - optional override; otherwise a persistent UUID is generated

The normal login flow is Token20 -> CheckPhone (SMS request) -> Token80.
The optional --all flag follows the history response's meta.pagination.total.
BeginVerifyPhone/VerifyPhone are a separate phone-change/verification flow and
are not part of the ordinary login path shown by VerifyPinViewModel.
The SMS PIN and phone are requested interactively and are never written to disk.
Tokens are kept in memory only.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import requests

PERSONAL_CABINET_BASE = os.getenv(
    "AUCHAN_PERSONAL_CABINET_BASE", "https://mobile.auchan.ru/lk"
).rstrip("/")
LOYMAX_BASE = os.getenv(
    "AUCHAN_LOYMAX_BASE", "https://mobile.auchan.ru/ecom"
).rstrip("/")

TIMEOUT = 35
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "MyAuchan/4.1.2",
    "Accept": "application/json",
})


class AuchanError(RuntimeError):
    pass


def get_device_id(explicit: str | None = None) -> str:
    """Reproduce BaseDeviceService.Udid: persist Guid.NewGuid() under udid_key."""
    override = (explicit or os.getenv("AUCHAN_DEVICE_ID") or "").strip()
    if override:
        return override

    config_root = Path(
        os.getenv("AUCHAN_CONFIG_DIR")
        or (Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "auchan-purchases")
    ).expanduser()
    device_file = config_root / "udid_key"
    try:
        config_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(config_root, 0o700)
        except OSError:
            pass
        if device_file.exists():
            value = device_file.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = str(uuid.uuid4())
        # Exclusive creation avoids replacing an ID if two instances start together.
        try:
            fd = os.open(device_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            existing = device_file.read_text(encoding="utf-8").strip()
            if existing:
                return existing
            raise AuchanError(f"Файл идентификатора пуст: {device_file}")
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value + "\n")
        return value
    except OSError as exc:
        raise AuchanError(f"Не удалось сохранить DeviceId в {device_file}: {exc}") from exc


def request_json(method: str, url: str, **kwargs: Any) -> Any:
    try:
        response = SESSION.request(method, url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise AuchanError(f"Сетевая ошибка: {exc}") from exc

    if not response.ok:
        # Do not dump headers: they may contain tokens or account identifiers.
        body = response.text[:1200]
        raise AuchanError(
            f"HTTP {response.status_code} от {url}\n"
            f"Ответ сервера: {body}"
        )
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise AuchanError(
            f"Сервер вернул не JSON (HTTP {response.status_code}): "
            f"{response.text[:500]}"
        ) from exc


def token_value(payload: Any) -> str:
    """Extract TokenDto token without assuming one JSON casing."""
    if not isinstance(payload, dict):
        raise AuchanError("Неожиданный ответ TokenDto: ожидался JSON-объект")
    candidates = ("token", "Token", "access_token", "accessToken", "AccessToken")
    for key in candidates:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    # Some API wrappers nest DTO data.
    for key in ("data", "Data", "result", "Result"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            for candidate in candidates:
                value = nested.get(candidate)
                if isinstance(value, str) and value:
                    return value
    raise AuchanError(
        "В ответе TokenDto не найдено поле token. "
        "Проверьте фактический JSON ответа и версию API."
    )


def login_by_sms(phone: str, device_id: str, need_hash: bool) -> str:
    token20_password = os.getenv("AUCHAN_TOKEN20_PASSWORD")
    if not token20_password:
        raise AuchanError(
            "Не задан AUCHAN_TOKEN20_PASSWORD. Это значение Token20 "
            "из конфигурации приложения; оно не равно SMS-коду."
        )

    # IPersonalCabinetApi.Token20:
    # GET /clientauth/token; login/password are HTTP headers.
    token20_payload = request_json(
        "GET",
        f"{PERSONAL_CABINET_BASE}/clientauth/token",
        headers={
            "login": "checkmail_user",
            "password": token20_password,
        },
    )
    token20 = token_value(token20_payload)

    # IPersonalCabinetApi.CheckPhone:
    # GET /clientprofile/checkphone with source: 1 and needHash query.
    check_payload = request_json(
        "GET",
        f"{PERSONAL_CABINET_BASE}/clientprofile/checkphone",
        headers={
            "access_token": token20,
            "phone": phone,
            "deviceid": device_id,
            "source": "1",
        },
        params={"needHash": "true" if need_hash else "false"},
    )

    # VerifyPinViewModel obtains Token80ByPhone(phone, pin); TokenService
    # sends GET /clientauth/token with phone/password/deviceid headers.
    # PinInteractor.VerifyPin then validates by requesting the profile; the
    # purchase-history client can call history directly with this Token80.
    print("Запрос проверки телефона выполнен.")
    if isinstance(check_payload, dict):
        # CheckPhoneDto.Result is an int mapped directly to PhoneStatus;
        # resultmessage is the server-provided explanation. Do not infer
        # enum labels because their numeric mapping is not in this report.
        status = first_value(check_payload, "result", "Result")
        message = first_value(check_payload, "resultmessage", "ResultMessage")
        if status != "":
            print(f"Результат CheckPhone: {status}")
        if message:
            print(f"Сообщение CheckPhone: {message}")

    pin = getpass.getpass("Введите SMS-код: ").strip()
    if not re.fullmatch(r"\d{4}", pin):
        raise AuchanError("Ожидался четырёхзначный SMS-код.")

    token80_payload = request_json(
        "GET",
        f"{PERSONAL_CABINET_BASE}/clientauth/token",
        headers={
            "phone": phone,
            "password": pin,
            "deviceid": device_id,
        },
    )
    return token_value(token80_payload)


def verify_token80(token80: str) -> dict[str, Any]:
    """Match PinInteractor.VerifyPin -> ProfileInteractor.GetProfile."""
    result = request_json(
        "GET",
        f"{LOYMAX_BASE}/cmd/profile",
        headers={
            "X-Token-CMD": token80,
            "Content-Type": "application/json",
        },
    )
    if not isinstance(result, dict):
        raise AuchanError("Проверка Token80 не вернула объект профиля.")
    profile = result.get("profile", result.get("Profile"))
    if profile is None:
        # Don't reject alternate server wrappers if the request succeeded;
        # retain the actual response for diagnostics in the caller.
        print("Предупреждение: HTTP-проверка профиля прошла, но поле profile не найдено.")
    return result


def get_history_page(token80: str, offset: int, limit: int) -> dict[str, Any]:
    # ILoymaxLoyaltyApi.LoyaltyHistory:
    # POST /lk/loyalty/history, X-Token-CMD, JSON body.
    # LoyaltyProgramPurchasesHistoryService.GetHistoryAsync sends filter.id=null
    # when no history filter is selected.
    body = {
        "channel": "M",
        "pagination": {"offset": offset, "limit": limit},
        "filter": {"id": None},
    }
    result = request_json(
        "POST",
        f"{LOYMAX_BASE}/lk/loyalty/history",
        headers={
            "X-Token-CMD": token80,
            "Content-Type": "application/json",
        },
        json=body,
    )
    if not isinstance(result, dict):
        raise AuchanError("Неожиданный формат ответа истории покупок.")
    return result


def history_total(payload: dict[str, Any]) -> int | None:
    meta = first_value(payload, "meta", "Meta")
    if not isinstance(meta, dict):
        return None
    pagination = first_value(meta, "pagination", "Pagination")
    if not isinstance(pagination, dict):
        return None
    value = first_value(pagination, "total", "Total")
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def get_history(token80: str, offset: int, limit: int, all_pages: bool = False) -> dict[str, Any]:
    first = get_history_page(token80, offset, limit)
    if not all_pages:
        return first

    merged = list(unwrap_items(first))
    total = history_total(first)
    next_offset = offset + len(merged)
    # A hard cap prevents a malformed pagination response from causing an
    # endless loop. Normal termination is total reached or a short page.
    for _ in range(1000):
        if total is not None and offset + len(merged) >= total:
            break
        page = get_history_page(token80, next_offset, limit)
        page_items = unwrap_items(page)
        if not page_items:
            break
        merged.extend(page_items)
        next_offset += len(page_items)
        if total is None and len(page_items) < limit:
            break
        if total is None:
            total = history_total(page)

    data_key = "data" if "data" in first else "Data"
    data = first.get(data_key)
    if not isinstance(data, dict):
        data = {}
        first[data_key] = data
    items_key = "items" if "items" in data else "Items"
    data[items_key] = merged
    return first


def get_receipt_details(token80: str, cheque_number: str) -> dict[str, Any]:
    # The decompiled service sends the chequeNumber as a history filter.
    body = {
        "channel": "M",
        "pagination": {"offset": 0, "limit": 1},
        "filter": {"chequeNumber": cheque_number},
    }
    result = request_json(
        "POST",
        f"{LOYMAX_BASE}/lk/loyalty/history",
        headers={
            "X-Token-CMD": token80,
            "Content-Type": "application/json",
        },
        json=body,
    )
    return result if isinstance(result, dict) else {"response": result}


def unwrap_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", payload.get("Data", {}))
    if isinstance(data, dict):
        items = data.get("items", data.get("Items", []))
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    return []


def first_value(obj: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
    return ""


def print_history(items: list[dict[str, Any]]) -> None:
    if not items:
        print("Покупок в полученной странице не найдено.")
        return
    print(f"\nПолучено покупок: {len(items)}\n")
    for index, item in enumerate(items, 1):
        # TransactionDetailsDto / LoyaltyProgramMappers use these DTO fields.
        date = first_value(item, "date", "Date")
        store = first_value(item, "locationName", "LocationName", "location", "Location")
        total = first_value(item, "totalValue", "TotalValue")
        number = first_value(item, "transactionNumber", "TransactionNumber")
        operation = first_value(item, "typeName", "TypeName", "operationType", "OperationType")
        print(f"{index:>3}. {date} | {store} | {total} | № {number} | {operation}")
        order = first_value(item, "orderDetails", "OrderDetails")
        if isinstance(order, dict):
            products = first_value(order, "products", "Products")
            if isinstance(products, list):
                for product in products:
                    if not isinstance(product, dict):
                        continue
                    name = first_value(product, "name", "Name")
                    quantity = first_value(product, "quantity", "Quantity")
                    value = first_value(product, "value", "Value")
                    code = first_value(product, "code", "Code")
                    print(f"      - {name} | кол-во: {quantity} | сумма: {value} | код: {code}")
        if not any((date, store, total, number, operation)):
            print("     " + json.dumps(item, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Авторизация Auchan по SMS и выгрузка истории покупок"
    )
    parser.add_argument("--phone", help="Номер телефона; если не указан, будет запрошен")
    parser.add_argument("--device-id",
                        help="переопределить deviceid (по умолчанию постоянный UUID в ~/.config/auchan-purchases/udid_key)")
    parser.add_argument("--need-hash", dest="need_hash", action="store_true",
                        default=True, help="Передать needHash=true (режим по умолчанию)")
    parser.add_argument("--no-hash", dest="need_hash", action="store_false",
                        help="Передать needHash=false (если устройство/сервер этого требует)")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--all", dest="all_pages", action="store_true",
                        help="Загрузить все страницы истории, используя meta.pagination.total")
    parser.add_argument("--cheque-number", action="append", default=[],
                        help="Дополнительно запросить один чек по chequeNumber; можно повторять")
    parser.add_argument("--json", dest="json_path",
                        help="Сохранить полученный результат в JSON-файл")
    args = parser.parse_args()

    phone = (args.phone or input("Телефон (в международном формате): ")).strip()
    phone = re.sub(r"[^\d+]", "", phone)
    if not re.fullmatch(r"\+?\d{10,15}", phone):
        raise AuchanError("Проверьте формат номера телефона.")
    if args.limit < 1 or args.limit > 100:
        raise AuchanError("--limit должен быть в диапазоне 1..100.")

    device_id = get_device_id(args.device_id)
    print("DeviceId загружен из постоянной конфигурации.")
    token80 = login_by_sms(phone, device_id, args.need_hash)
    print("Token80 получен; проверяю его через GET /cmd/profile...")
    profile = verify_token80(token80)
    print("Проверка профиля выполнена; Token80 принят API.")

    history = get_history(token80, args.offset, args.limit, args.all_pages)
    items = unwrap_items(history)
    total = history_total(history)
    if total is not None:
        print(f"Всего записей по meta.pagination.total: {total}")
    print_history(items)

    # Do not persist the profile response: it contains personal information
    # unrelated to the requested purchase-history export.
    output: dict[str, Any] = {"history": history}
    if args.cheque_number:
        details: list[dict[str, Any]] = []
        for cheque in args.cheque_number:
            try:
                details.append({
                    "chequeNumber": cheque,
                    "response": get_receipt_details(token80, cheque),
                })
            except AuchanError as exc:
                print(f"Не удалось загрузить детали чека {cheque}: {exc}",
                      file=sys.stderr)
        output["receipt_details"] = details

    if args.json_path:
        path = os.path.abspath(args.json_path)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(output, file, ensure_ascii=False, indent=2)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        print(f"\nJSON сохранён: {path} (права доступа 600, если поддерживаются).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuchanError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("\nОтменено.", file=sys.stderr)
        raise SystemExit(130)
