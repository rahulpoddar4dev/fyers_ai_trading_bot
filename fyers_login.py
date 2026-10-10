import json
import os
import subprocess
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from requests.exceptions import RequestException
import config
from fyers_broker import FyersBrokerAdapter, FyersTokenException
from market_time import today_date

fyers = FyersBrokerAdapter(
    client_id=config.FYERS_CLIENT_ID,
    secret_key=config.FYERS_SECRET_KEY,
    redirect_uri=config.FYERS_REDIRECT_URI,
    timeout=config.FYERS_REQUEST_TIMEOUT,
)

BASE_DIR = Path(__file__).resolve().parent
SESSION_FILE = BASE_DIR / "fyers_session.json"


def _active_api_secret() -> str:
    return config.FYERS_SECRET_KEY


def _fyers_call_with_retries(label, callback):
    last_error = None

    for attempt in range(1, config.FYERS_LOGIN_RETRIES + 1):
        try:
            return callback()
        except (RequestException, ConnectionResetError, TimeoutError) as error:
            last_error = error
            if attempt == config.FYERS_LOGIN_RETRIES:
                break
            print(
                f"{label} timed out. Retrying "
                f"{attempt}/{config.FYERS_LOGIN_RETRIES} in {config.FYERS_LOGIN_RETRY_SLEEP}s..."
            )
            time.sleep(config.FYERS_LOGIN_RETRY_SLEEP)

    raise last_error


def save_access_token(access_token):
    session_data = {
        "date": today_date().isoformat(),
        "broker": "FYERS",
        "access_token": access_token,
    }
    SESSION_FILE.write_text(json.dumps(session_data, indent=2))


def load_saved_access_token():
    env_token = os.getenv("FYERS_ACCESS_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()

    if not SESSION_FILE.exists():
        return None

    try:
        session_data = json.loads(SESSION_FILE.read_text())
    except json.JSONDecodeError:
        return None

    if session_data.get("date") != today_date().isoformat():
        return None

    return session_data.get("access_token")


_SAVED_PROFILE_CACHE = {}


def try_saved_login(silent: bool = False):
    access_token = load_saved_access_token()
    if not access_token:
        return None

    fyers.set_access_token(access_token)

    if _SAVED_PROFILE_CACHE.get("token") == access_token and _SAVED_PROFILE_CACHE.get("profile"):
        profile = _SAVED_PROFILE_CACHE["profile"]
    else:
        try:
            profile = _fyers_call_with_retries("Saved Fyers session check", fyers.profile)
            _SAVED_PROFILE_CACHE["token"] = access_token
            _SAVED_PROFILE_CACHE["profile"] = profile
        except FyersTokenException:
            return None
        except (RequestException, ConnectionResetError, TimeoutError) as error:
            if not silent:
                print("Using saved Fyers session.")
                print(f"Profile check skipped because Fyers/network timed out: {error}")
            return fyers

    if not silent:
        print("Using saved Fyers session.")
        if profile and isinstance(profile, dict) and "user_name" in profile:
            print(f"Welcome: {profile['user_name']}")
    return fyers


class _FyersCallbackHandler(BaseHTTPRequestHandler):
    auth_code = None
    error_message = None
    dashboard_url = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        token = params.get("auth_code", [""])[0]
        status = params.get("status", [""])[0] or params.get("s", [""])[0]
        error = params.get("error", [""])[0] or params.get("message", [""])[0]

        if token and (status.lower() in {"", "success", "ok"}):
            _FyersCallbackHandler.auth_code = token
            self._send_page(
                "Fyers login successful. Redirecting to dashboard...",
                redirect=True,
            )
            return

        if parsed.path not in {config.FYERS_CALLBACK_PATH, "/fyers/callback", "/callback", "/"}:
            self._send_page(
                "Fyers callback server is running. Please complete Fyers login.",
                redirect=False,
            )
            return

        _FyersCallbackHandler.error_message = error or "Fyers login callback did not include auth_code."
        self._send_page(_FyersCallbackHandler.error_message, redirect=False)

    def _send_page(self, message: str, redirect: bool):
        dashboard_url = _FyersCallbackHandler.dashboard_url
        redirect_tag = ""
        if redirect and dashboard_url:
            redirect_tag = f'<meta http-equiv="refresh" content="1; url={dashboard_url}">'

        body = f"""
        <html>
          <head>
            <title>Fyers Login</title>
            {redirect_tag}
            <style>
              body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 40px; }}
              .box {{ max-width: 640px; border: 1px solid #d8dee9; border-radius: 8px; padding: 24px; }}
              a {{ color: #3157c8; }}
            </style>
          </head>
          <body>
            <div class="box">
              <h2>{message}</h2>
              <p>You can return to the terminal now.</p>
              {f'<p><a href="{dashboard_url}">Open dashboard</a></p>' if dashboard_url else ''}
            </div>
          </body>
        </html>
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        return


def _callback_url() -> str:
    return (
        f"http://{config.FYERS_CALLBACK_PUBLIC_HOST}:"
        f"{config.FYERS_CALLBACK_PORT}{config.FYERS_CALLBACK_PATH}"
    )


def _extract_auth_code(value: str) -> str:
    value = value.strip()
    if "auth_code=" not in value and "code=" not in value:
        return value

    parsed = urlparse(value)
    qs = parse_qs(parsed.query)
    token = qs.get("auth_code", [""])[0]
    return token or value


def _read_browser_auth_code() -> str | None:
    if not config.FYERS_READ_BROWSER_URL_FALLBACK:
        return None

    script = f'''
    tell application "{config.FYERS_BROWSER_APP}"
      if (count of windows) is 0 then return ""
      set activeUrl to URL of active tab of front window
      return activeUrl
    end tell
    '''

    try:
        current_url = subprocess.check_output(
            ["osascript", "-e", script],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
    except Exception:
        return None

    if "auth_code=" not in current_url:
        return None

    return _extract_auth_code(current_url)


def _is_local_callback_redirect() -> bool:
    redirect_uri = (config.FYERS_REDIRECT_URI or "").lower()
    return "127.0.0.1" in redirect_uri or "localhost" in redirect_uri


def _capture_auth_code(login_url: str, dashboard_url: str | None = None) -> str | None:
    if not _is_local_callback_redirect():
        print("Opening Fyers login in browser...")
        print(f"Redirect URI configured: {config.FYERS_REDIRECT_URI}")
        try:
            webbrowser.open(login_url)
        except Exception:
            pass
        return None

    _FyersCallbackHandler.auth_code = None
    _FyersCallbackHandler.error_message = None
    _FyersCallbackHandler.dashboard_url = dashboard_url

    try:
        server = ThreadingHTTPServer(
            (config.FYERS_CALLBACK_HOST, config.FYERS_CALLBACK_PORT),
            _FyersCallbackHandler,
        )
    except OSError as error:
        print(f"Auto login callback server could not start: {error}")
        return None

    server.timeout = 1
    print("Opening Fyers login in browser...")
    print(f"Expected Redirect URL in Fyers app: {config.FYERS_REDIRECT_URI}")
    print("If browser does not open, use this URL:")
    print(login_url)
    print(f"Waiting up to {config.FYERS_CALLBACK_TIMEOUT_SECONDS} seconds for Fyers callback...")
    ignored_browser_token = _read_browser_auth_code()
    webbrowser.open(login_url)

    started_at = time.time()
    try:
        while time.time() - started_at < config.FYERS_CALLBACK_TIMEOUT_SECONDS:
            server.handle_request()
            if _FyersCallbackHandler.auth_code:
                return _FyersCallbackHandler.auth_code
            browser_token = _read_browser_auth_code()
            if browser_token and browser_token != ignored_browser_token:
                print("Fyers auth_code captured from browser URL.")
                if dashboard_url:
                    webbrowser.open(dashboard_url)
                return browser_token
            if _FyersCallbackHandler.error_message:
                print(f"Fyers callback error: {_FyersCallbackHandler.error_message}")
                return None
    finally:
        server.server_close()

    print("Auto login timed out.")
    return None


def _wait_for_dashboard_login(dashboard_url: str | None) -> bool:
    target_url = config.PUBLIC_URL
    if not target_url:
        return False

    print("Fyers session is not active.")
    print(f"Open dashboard and click Fyers Login: {target_url}")
    print(f"Waiting up to {config.FYERS_CALLBACK_TIMEOUT_SECONDS} seconds for dashboard login...")

    started_at = time.time()
    while time.time() - started_at < config.FYERS_CALLBACK_TIMEOUT_SECONDS:
        if load_saved_access_token():
            return True
        time.sleep(1)

    print("Dashboard Fyers login wait timed out.")
    return False


def login(dashboard_url: str | None = None):
    saved_fyers = try_saved_login()
    if saved_fyers:
        return saved_fyers

    env_token = os.getenv("FYERS_AUTH_CODE")
    if env_token:
        auth_code = _extract_auth_code(env_token)
        try:
            data = _fyers_call_with_retries(
                "Fyers session generation from env FYERS_AUTH_CODE",
                lambda: fyers.generate_session(
                    auth_code,
                    api_secret=_active_api_secret(),
                ),
            )
            fyers.set_access_token(data["access_token"])
            save_access_token(data["access_token"])
            print("Login via FYERS_AUTH_CODE successful!")
            profile = _fyers_call_with_retries("Fyers profile fetch", fyers.profile)
            print(f"Welcome: {profile['user_name']}")
            return fyers
        except Exception as error:
            print(f"Failed to use env FYERS_AUTH_CODE: {error}")

    if _wait_for_dashboard_login(dashboard_url):
        saved_fyers = try_saved_login()
        if saved_fyers:
            return saved_fyers

    login_url = fyers.login_url()
    auth_code = None

    if config.FYERS_AUTO_LOGIN and sys.stdin.isatty():
        auth_code = _capture_auth_code(login_url, dashboard_url)

    if not auth_code:
        target_url = config.PUBLIC_URL or dashboard_url or login_url
        print("Open this URL in browser to login to Fyers:")
        print(login_url)
        print()

        if not sys.stdin.isatty():
            print("Non-interactive terminal detected (cloud server / Render).")
            print(f"Please open the dashboard URL to login or paste Fyers auth_code: {target_url}")
            print("Waiting for Fyers login to be completed via Dashboard...")
            last_msg_at = 0
            while True:
                saved_fyers = try_saved_login()
                if saved_fyers:
                    return saved_fyers
                now = time.time()
                if now - last_msg_at > 30:
                    print(f"[{time.strftime('%H:%M:%S')}] Still waiting for Fyers login via dashboard ({target_url})...")
                    last_msg_at = now
                time.sleep(2)

        pasted_value = input("Paste Fyers auth_code OR full redirected URL here: ").strip()
        if not pasted_value and load_saved_access_token():
            saved_fyers = try_saved_login()
            if saved_fyers:
                return saved_fyers
        auth_code = _extract_auth_code(pasted_value)

    for attempt in range(1, 3):
        try:
            data = _fyers_call_with_retries(
                "Fyers session generation",
                lambda: fyers.generate_session(
                    auth_code,
                    api_secret=_active_api_secret(),
                ),
            )
            break
        except FyersTokenException as error:
            if attempt >= 2:
                raise
            print(f"Fyers auth_code was invalid/expired: {error}")
            if not sys.stdin.isatty():
                print("Please complete login again via Dashboard URL.")
                return login(dashboard_url=dashboard_url)
            print("Please login again and paste the fresh redirected URL / auth_code.")
            print("Login URL:")
            print(login_url)
            pasted_value = input("Paste fresh Fyers auth_code OR full redirected URL here: ")
            auth_code = _extract_auth_code(pasted_value)

    fyers.set_access_token(data["access_token"])
    save_access_token(data["access_token"])
    print("Fyers login successful!")
    profile = _fyers_call_with_retries("Fyers profile fetch", fyers.profile)
    print(f"Welcome: {profile['user_name']}")

    return fyers


if __name__ == "__main__":
    login()
