#!/usr/bin/env python3
"""
Auchan purchase history client.

Based on the My Auchan 4.1.2 API declarations in:
https://github.com/definitly486/AuchanReverseEngineering

Required environment:
  AUCHAN_TOKEN20_PASSWORD  - optional override; Production value is embedded below
  AUCHAN_DEVICE_ID         - optional override; otherwise a persistent UUID is generated

The normal login flow is Token20 -> CheckPhone (SMS request) -> Token80.
The optional --all flag follows the history response's meta.pagination.total.
BeginVerifyPhone/VerifyPhone are a separate phone-change/verification flow and
are not part of the ordinary login path shown by VerifyPinViewModel.
The SMS PIN and phone are requested interactively and are never written to disk.
Token80 is cached locally with restrictive permissions (0600) so SMS login
is normally needed only once. Use --login to force a new SMS login or --logout
to remove the cached session.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
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

# TokenService.Token20() in the decompiled My Auchan 4.1.2 app selects this
# value for Environment.Production. Environment variables can override it.
TOKEN20_PRODUCTION_PASSWORD = "LqX~A4gR"

TIMEOUT = 35
VERBOSE = False
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "MyAuchan/4.1.2",
    "Accept": "application/json",
})


class AuchanError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code




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


def _mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) < 5:
        return "<redacted>"
    return "*" * (len(digits) - 2) + digits[-2:]


def _redact(value: Any, key: str = "") -> Any:
    """Redact credentials and personal identifiers before verbose logging."""
    sensitive = re.compile(r"token|password|phone|pin|device.?id|authorization|cookie|secret|udid", re.I)
    if sensitive.search(key):
        if "phone" in key.lower() and isinstance(value, str):
            return _mask_phone(value)
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        # Also hide phone numbers if they appear inside free-form server messages.
        return re.sub(r"\+?\d[\d ()-]{8,}\d", lambda m: _mask_phone(m.group(0)), value)
    return value


def _debug(message: str) -> None:
    if VERBOSE:
        print(f"[verbose] {message}", file=sys.stderr, flush=True)


def request_json(method: str, url: str, *, allow_statuses: tuple[int, ...] = (), **kwargs: Any) -> Any:
    safe_headers = _redact(kwargs.get("headers", {}))
    safe_params = _redact(kwargs.get("params", {}))
    safe_json = _redact(kwargs.get("json")) if "json" in kwargs else None
    _debug(f"--> {method.upper()} {url}")
    if safe_headers:
        _debug("request headers: " + json.dumps(safe_headers, ensure_ascii=False))
    if safe_params:
        _debug("query params: " + json.dumps(safe_params, ensure_ascii=False))
    if safe_json is not None:
        _debug("request JSON: " + json.dumps(safe_json, ensure_ascii=False))

    started = time.monotonic()
    try:
        response = SESSION.request(method, url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        _debug(f"network exception after {time.monotonic() - started:.2f}s: {exc}")
        raise AuchanError(f"Сетевая ошибка: {exc}") from exc

    elapsed = time.monotonic() - started
    _debug(
        f"<-- HTTP {response.status_code} in {elapsed:.2f}s; "
        f"content-type={response.headers.get('Content-Type', '<none>')}; "
        f"content-length={response.headers.get('Content-Length', '<unknown>')}"
    )
    if VERBOSE and response.content:
        try:
            preview = _redact(response.json())
            rendered = json.dumps(preview, ensure_ascii=False, indent=2)
        except ValueError:
            rendered = str(_redact(response.text))
        if len(rendered) > 5000:
            rendered = rendered[:5000] + "\n... <truncated>"
        _debug("response body:\n" + rendered)

    if not response.ok and response.status_code not in allow_statuses:
        # Do not dump headers: they may contain tokens or account identifiers.
        try:
            body = json.dumps(_redact(response.json()), ensure_ascii=False)
        except ValueError:
            body = str(_redact(response.text))
        raise AuchanError(
            f"HTTP {response.status_code} от {url}\n"
            f"Ответ сервера: {body[:1200]}",
            status_code=response.status_code,
        )
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise AuchanError(
            f"Сервер вернул не JSON (HTTP {response.status_code}): "
            f"{str(_redact(response.text))[:500]}"
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


def config_dir() -> Path:
    return Path(
        os.getenv("AUCHAN_CONFIG_DIR")
        or (Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "auchan-purchases")
    ).expanduser()


def session_file() -> Path:
    return config_dir() / "session.json"


def load_cached_session() -> dict[str, str] | None:
    path = session_file()
    try:
        if not path.is_file():
            return None
        # Do not accept a session file accessible to group/other users.
        if os.name == "posix" and (path.stat().st_mode & 0o077):
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        data = json.loads(path.read_text(encoding="utf-8"))
        token = data.get("token80") if isinstance(data, dict) else None
        phone = data.get("phone") if isinstance(data, dict) else None
        if isinstance(token, str) and token and isinstance(phone, str) and phone:
            return {"token80": token, "phone": phone}
    except (OSError, ValueError, TypeError):
        return None
    return None


def save_cached_session(token80: str, phone: str) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    path = session_file()
    temporary = path.with_suffix(".json.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"token80": token80, "phone": phone}, stream)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_cached_session() -> None:
    try:
        session_file().unlink()
    except FileNotFoundError:
        pass


def login_by_sms(phone: str, device_id: str, need_hash: bool) -> str:
    _debug("Шаг 1/3: получение Token20")
    token20_password = os.getenv(
        "AUCHAN_TOKEN20_PASSWORD", TOKEN20_PRODUCTION_PASSWORD
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
    _debug("Token20 получен (значение скрыто).")

    # IPersonalCabinetApi.CheckPhone:
    # GET /clientprofile/checkphone with source: 1 and needHash query.
    _debug(f"Шаг 2/3: CheckPhone; needHash={need_hash}; DeviceId скрыт.")
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
        # Auchan returns HTTP 400 with a meaningful CheckPhoneDto when an
        # SMS was already sent. Treat it as a protocol response, not a fatal
        # transport error; all other CheckPhone errors are handled below.
        allow_statuses=(400,),
    )

    # A repeated CheckPhone request can return HTTP 400 while explicitly
    # saying that an SMS is already pending. In that case reuse the existing
    # code; do not retry and risk triggering rate limits.
    if isinstance(check_payload, dict):
        check_error = first_value(check_payload, "error", "Error")
        check_result = str(first_value(check_payload, "result", "Result"))
        check_message = str(first_value(check_payload, "msg", "Msg", "resultmessage", "ResultMessage"))
        already_sent = (
            check_result == "2"
            or "смс с кодом уже отправлено" in check_message.casefold()
            or "sms already sent" in check_message.casefold()
        )
        if already_sent:
            print("Сервер сообщает, что SMS уже отправлено. Повторный запрос не выполняю.")
            print("Введите код из ранее отправленного SMS (если сообщение ещё не пришло, подождите перед повторной попыткой).")
        elif str(check_error) not in ("", "0", "None"):
            raise AuchanError(
                "CheckPhone отклонён сервером: "
                + (check_message or json.dumps(_redact(check_payload), ensure_ascii=False))
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

    _debug("CheckPhone завершён; ожидается SMS-код.")
    pin = getpass.getpass("Введите SMS-код: ").strip()
    if not re.fullmatch(r"\d{4}", pin):
        raise AuchanError("Ожидался четырёхзначный SMS-код.")

    _debug("Шаг 3/3: получение Token80 по SMS-коду (код скрыт).")
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
    # The live API rejects pagination.limit > 10. Treat --limit as a requested
    # page size and cap it to the server's confirmed maximum.
    limit = min(limit, 10)
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
    parser.add_argument("--phone", help="Номер телефона; при сохранённой сессии можно не указывать")
    parser.add_argument("--login", action="store_true",
                        help="Принудительно выполнить новый вход по SMS, заменив сохранённую сессию")
    parser.add_argument("--logout", action="store_true",
                        help="Удалить сохранённую сессию и завершить работу")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Подробный HTTP-лог; токены, пароль, SMS-код и телефон маскируются")
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
    parser.add_argument("--check-profile", action="store_true",
                        help="Дополнительно проверить Token80 через GET /cmd/profile (может блокироваться WAF)")
    parser.add_argument("--cheque-number", action="append", default=[],
                        help="Дополнительно запросить один чек по chequeNumber; можно повторять")
    parser.add_argument("--json", dest="json_path",
                        help="Сохранить полученный результат в JSON-файл")
    args = parser.parse_args()

    global VERBOSE
    VERBOSE = args.verbose
    if VERBOSE:
        _debug("Подробный режим включён. Секретные заголовки и персональные данные маскируются.")

    if args.logout:
        clear_cached_session()
        print("Сохранённая авторизация удалена.")
        return 0

    if args.limit < 1 or args.limit > 100:
        raise AuchanError("--limit должен быть в диапазоне 1..100; фактический размер страницы ограничен сервером до 10.")

    cached = None if args.login else load_cached_session()
    supplied_phone = (args.phone or "").strip()
    if cached and not supplied_phone:
        phone = cached["phone"]
    else:
        phone = supplied_phone or input("Телефон (в международном формате): ").strip()
    phone = re.sub(r"[^\d+]", "", phone)
    if not re.fullmatch(r"\+?\d{10,15}", phone):
        raise AuchanError("Проверьте формат номера телефона.")

    device_id = get_device_id(args.device_id)
    print("DeviceId загружен из постоянной конфигурации.")
    _debug(f"Personal Cabinet API: {PERSONAL_CABINET_BASE}")
    _debug(f"Loymax API: {LOYMAX_BASE}")
    _debug(f"Номер телефона: {_mask_phone(phone)}")
    _debug(f"Режим needHash: {args.need_hash}")

    token80 = None
    if cached and cached.get("phone") == phone:
        token80 = cached["token80"]
        print("Загружена сохранённая авторизация; SMS повторно не запрашивается.")
    elif cached:
        print("Номер отличается от сохранённой сессии; потребуется новый вход.")

    if token80 is None:
        token80 = login_by_sms(phone, device_id, args.need_hash)
        save_cached_session(token80, phone)
        print(f"Token80 получен и сохранён в {session_file()} (права 600).")
    if args.check_profile:
        print("Дополнительно проверяю Token80 через GET /cmd/profile...")
        try:
            verify_token80(token80)
            print("Проверка профиля выполнена.")
        except AuchanError as exc:
            print(
                "Предупреждение: проверка /cmd/profile не прошла. "
                "Это не доказывает недействительность Token80; продолжаю запрос истории.\n"
                f"{exc}",
                file=sys.stderr,
            )

    try:
        history = get_history(token80, args.offset, args.limit, args.all_pages)
    except AuchanError as exc:
        if exc.status_code != 401 or args.login:
            raise
        print("Сохранённый Token80 отклонён (HTTP 401). Удаляю сессию и запрашиваю новый SMS-вход.", file=sys.stderr)
        clear_cached_session()
        token80 = login_by_sms(phone, device_id, args.need_hash)
        save_cached_session(token80, phone)
        print("Новая авторизация сохранена.")
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
