"""계정·동기화 HTTP API. app.py는 라우트만 연결한다.

로그인/회원가입/세션, 사용자 설정, 데스크톱 페어링 코드를 담당한다.
비즈니스 로직은 sync.* / app 재수출에 있고, 여기는 HTTP 어댑터만 둔다.
테스트가 app.sign_in 등을 패치하므로 sync를 직접 묶지 않고 app을 경유한다.
"""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlparse


def _get_current_user():
    import app

    return app.get_current_user()


def _get_supabase_client():
    import app

    return app.get_supabase_client()


def _sign_in(email: str, password: str):
    import app

    return app.sign_in(email, password)


def _sign_out():
    import app

    return app.sign_out()


def _sign_up(email: str, password: str):
    import app

    return app.sign_up(email, password)


def _current_user_settings_payload(device_type: str):
    import app

    return app.current_user_settings_payload(device_type)


def _save_user_settings(user_id: str, primary_device_type: str, *, agree: bool):
    import app

    return app.save_user_settings(user_id, primary_device_type, agree=agree)


def _generate_pairing_code(device_id: str):
    import app

    return app.generate_pairing_code(device_id)


def _get_desktop_device_id():
    import app

    return app.get_desktop_device_id()


def try_handle_get(handler, path: str) -> bool:
    if path == "/api/auth/me":
        handler.send_json({"user": _get_current_user()})
        return True

    if path == "/api/user-settings":
        query = parse_qs(urlparse(handler.path).query)
        device_type = (query.get("device_type") or [""])[0]
        payload = _current_user_settings_payload(device_type)
        if payload.get("error") == "sync_not_configured":
            handler.send_json(
                {"error": "sync_not_configured"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return True
        handler.send_json(payload)
        return True

    if path == "/api/pairing/code":
        if _get_supabase_client() is None:
            handler.send_json(
                {"error": "sync_not_configured"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return True
        result = _generate_pairing_code(_get_desktop_device_id())
        if not result:
            handler.api_error(
                "페어링 코드를 만들지 못했습니다.",
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return True
        handler.send_json(result)
        return True

    return False


def try_handle_post(handler, path: str, body: dict | None) -> bool:
    body = body or {}
    if path == "/api/auth/signup":
        result = _sign_up(
            str(body.get("email") or ""),
            str(body.get("password") or ""),
        )
        if not result.get("ok"):
            status = result.get("status") or HTTPStatus.BAD_REQUEST
            handler.api_error(
                str(result.get("error") or "회원가입에 실패했습니다."),
                HTTPStatus(int(status)),
            )
            return True
        handler.send_json(result)
        return True

    if path == "/api/auth/login":
        result = _sign_in(
            str(body.get("email") or ""),
            str(body.get("password") or ""),
        )
        if not result.get("ok"):
            status = result.get("status") or HTTPStatus.BAD_REQUEST
            handler.api_error(
                str(result.get("error") or "로그인에 실패했습니다."),
                HTTPStatus(int(status)),
            )
            return True
        handler.send_json({"ok": True, "user": result.get("user")})
        return True

    if path == "/api/auth/logout":
        handler.send_json(_sign_out())
        return True

    return False


def try_handle_put(handler, path: str, body: dict | None) -> bool:
    if path != "/api/user-settings":
        return False
    body = body or {}
    user = _get_current_user()
    if not user or not str(user.get("id") or "").strip():
        handler.api_error("로그인해 주세요.", HTTPStatus.UNAUTHORIZED)
        return True
    if _get_supabase_client() is None:
        handler.send_json(
            {"error": "sync_not_configured"},
            HTTPStatus.SERVICE_UNAVAILABLE,
        )
        return True
    try:
        settings = _save_user_settings(
            str(user["id"]),
            str(body.get("primary_device_type") or ""),
            agree=bool(body.get("agree")),
        )
    except RuntimeError as error:
        message = str(error or "").strip()
        if message == "sync_not_configured":
            handler.send_json(
                {"error": "sync_not_configured"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return True
        handler.api_error(
            message or "우선 기기 설정을 저장하지 못했습니다.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )
        return True
    handler.send_json({"ok": True, "settings": settings})
    return True
