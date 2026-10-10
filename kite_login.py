import json
import os
import subprocess
import sys
import time
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from kiteconnect import exceptions
from kiteconnect import KiteConnect
from requests.exceptions import RequestException
import config
from market_time import today_date

kite = KiteConnect(api_key=config.KITE_API_KEY, timeout=config.KITE_REQUEST_TIMEOUT)
BASE_DIR = Path(__file__).resolve().parent
SESSION_FILE = BASE_DIR / "kite_session.json"


def _kite_call_with_retries(label, callback):
    last_error = None

    for attempt in range(1, config.KITE_LOGIN_RETRIES + 1):
        try:
            return callback()
        except (RequestException, ConnectionResetError, TimeoutError) as error:
            last_error = error
            if attempt == config.KITE_LOGIN_RETRIES:
                break
            print(
                f"{label} timed out. Retrying "
                f"{attempt}/{config.KITE_LOGIN_RETRIES} in {config.KITE_LOGIN_RETRY_SLEEP}s..."
            )
            time.sleep(config.KITE_LOGIN_RETRY_SLEEP)

    raise last_error


def save_access_token(access_token):
    session_data = {
        "date": today_date().isoformat(),
        "access_token": access_token,
    }
    SESSION_FILE.write_text(json.dumps(session_data, indent=2))


def load_saved_access_token():
    env_token = os.getenv("KITE_ACCESS_TOKEN")
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

    kite.set_access_token(access_token)

    if _SAVED_PROFILE_CACHE.get("token") == access_token and _SAVED_PROFILE_CACHE.get("profile"):
        profile = _SAVED_PROFILE_CACHE["profile"]
    else:
        try:
            profile = _kite_call_with_retries("Saved Kite session check", kite.profile)
            _SAVED_PROFILE_CACHE["token"] = access_token
            _SAVED_PROFILE_CACHE["profile"] = profile
        except exceptions.TokenException:
            return None
        except (RequestException, ConnectionResetError, TimeoutError) as error:
            if not silent:
                print("Using saved Kite session.")
                print(f"Profile check skipped because Kite/network timed out: {error}")
            return kite

    if not silent:
        print("Using saved Kite session.")
        if profile and isinstance(profile, dict) and "user_name" in profile:
            print(f"Welcome: {profile['user_name']}")
    return kite


class _KiteCallbackHandler(BaseHTTPRequestHandler):
    request_token = None
    error_message = None
    dashboard_url = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        token = params.get("request_token", [""])[0]
        status = params.get("status", [""])[0]
        error = params.get("error", [""])[0] or params.get("message", [""])[0]

        if token and (status in {"", "success"}):
            _KiteCallbackHandler.request_token = token
            self._send_page(
                "Kite login successful. Redirecting to dashboard...",
                redirect=True,
            )
            return

        if parsed.path != config.KITE_CALLBACK_PATH:
            self._send_page(
                "Kite callback server is running. Please complete Zerodha login.",
                redirect=False,
            )
            return

        _KiteCallbackHandler.error_message = error or "Kite login callback did not include request_token."
        self._send_page(_KiteCallbackHandler.error_message, redirect=False)

    def _send_page(self, message: str, redirect: bool):
        dashboard_url = _KiteCallbackHandler.dashboard_url
        redirect_tag = ""
        if redirect and dashboard_url:
            redirect_tag = f'<meta http-equiv="refresh" content="1; url={dashboard_url}">'

        body = f"""
        <html>
          <head>
            <title>Kite Login</title>
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
        f"http://{config.KITE_CALLBACK_PUBLIC_HOST}:"
        f"{config.KITE_CALLBACK_PORT}{config.KITE_CALLBACK_PATH}"
    )


def _extract_request_token(value: str) -> str:
    value = value.strip()
    if "request_token=" not in value:
        return value

    parsed = urlparse(value)
    token = parse_qs(parsed.query).get("request_token", [""])[0]
    return token or value


def _read_browser_request_token() -> str | None:
    if not config.KITE_READ_BROWSER_URL_FALLBACK:
        return None

    script = f'''
    tell application "{config.KITE_BROWSER_APP}"
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

    if "request_token=" not in current_url:
        return None

    return _extract_request_token(current_url)


def _capture_request_token(login_url: str, dashboard_url: str | None = None) -> str | None:
    _KiteCallbackHandler.request_token = None
    _KiteCallbackHandler.error_message = None
    _KiteCallbackHandler.dashboard_url = dashboard_url

    try:
        server = ThreadingHTTPServer(
            (config.KITE_CALLBACK_HOST, config.KITE_CALLBACK_PORT),
            _KiteCallbackHandler,
        )
    except OSError as error:
        print(f"Auto login callback server could not start: {error}")
        return None

    server.timeout = 1
    print("Opening Kite login in browser...")
    print(f"Expected Redirect URL in Kite app: {_callback_url()}")
    print("If browser does not open, use this URL:")
    print(login_url)
    print(f"Waiting up to {config.KITE_CALLBACK_TIMEOUT_SECONDS} seconds for Kite callback...")
    if config.KITE_READ_BROWSER_URL_FALLBACK:
        print(f"Browser URL fallback enabled for {config.KITE_BROWSER_APP}.")
    ignored_browser_token = _read_browser_request_token()
    webbrowser.open(login_url)

    started_at = time.time()
    try:
        while time.time() - started_at < config.KITE_CALLBACK_TIMEOUT_SECONDS:
            server.handle_request()
            if _KiteCallbackHandler.request_token:
                return _KiteCallbackHandler.request_token
            browser_token = _read_browser_request_token()
            if browser_token and browser_token != ignored_browser_token:
                print("Request token captured from browser URL.")
                if dashboard_url:
                    webbrowser.open(dashboard_url)
                return browser_token
            if _KiteCallbackHandler.error_message:
                print(f"Kite callback error: {_KiteCallbackHandler.error_message}")
                return None
    finally:
        server.server_close()

    print("Auto login timed out.")
    return None


def _wait_for_dashboard_login(dashboard_url: str | None) -> bool:
    target_url = config.PUBLIC_URL or dashboard_url
    if not target_url:
        return False

    print("Kite session is not active.")
    print(f"Open dashboard and click Kite Login: {target_url}")
    print(f"Waiting up to {config.KITE_CALLBACK_TIMEOUT_SECONDS} seconds for dashboard login...")

    started_at = time.time()
    while time.time() - started_at < config.KITE_CALLBACK_TIMEOUT_SECONDS:
        if load_saved_access_token():
            return True
        time.sleep(1)

    print("Dashboard Kite login wait timed out.")
    return False


def login(dashboard_url: str | None = None):
    saved_kite = try_saved_login()
    if saved_kite:
        return saved_kite

    env_token = os.getenv("KITE_REQUEST_TOKEN")
    if env_token:
        request_token = _extract_request_token(env_token)
        try:
            data = _kite_call_with_retries(
                "Kite session generation from env KITE_REQUEST_TOKEN",
                lambda: kite.generate_session(
                    request_token,
                    api_secret=config.KITE_API_SECRET,
                ),
            )
            kite.set_access_token(data["access_token"])
            save_access_token(data["access_token"])
            print("Login via KITE_REQUEST_TOKEN successful!")
            profile = _kite_call_with_retries("Kite profile fetch", kite.profile)
            print(f"Welcome: {profile['user_name']}")
            return kite
        except Exception as error:
            print(f"Failed to use env KITE_REQUEST_TOKEN: {error}")

    if _wait_for_dashboard_login(dashboard_url):
        saved_kite = try_saved_login()
        if saved_kite:
            return saved_kite

    login_url = kite.login_url()
    request_token = None

    if config.KITE_AUTO_LOGIN and sys.stdin.isatty():
        request_token = _capture_request_token(login_url, dashboard_url)

    if not request_token:
        target_url = config.PUBLIC_URL or dashboard_url or login_url
        print("Open this URL in browser to login:")
        print(login_url)
        print()

        if not sys.stdin.isatty():
            print("Non-interactive terminal detected (cloud server / Render).")
            print(f"Please open the dashboard URL to login or paste request_token: {target_url}")
            print("Waiting for Kite login to be completed via Dashboard...")
            last_msg_at = 0
            while True:
                saved_kite = try_saved_login()
                if saved_kite:
                    return saved_kite
                now = time.time()
                if now - last_msg_at > 30:
                    print(f"[{time.strftime('%H:%M:%S')}] Still waiting for Kite login via dashboard ({target_url})...")
                    last_msg_at = now
                time.sleep(2)

        pasted_value = input("Paste request_token OR full redirected URL here: ")
        request_token = _extract_request_token(pasted_value)

    for attempt in range(1, 3):
        try:
            data = _kite_call_with_retries(
                "Kite session generation",
                lambda: kite.generate_session(
                    request_token,
                    api_secret=config.KITE_API_SECRET,
                ),
            )
            break
        except exceptions.TokenException as error:
            if attempt >= 2:
                raise
            print(f"Kite request_token was invalid/expired: {error}")
            if not sys.stdin.isatty():
                print("Please complete login again via Dashboard URL.")
                return login(dashboard_url=dashboard_url)
            print("Please login again and paste the fresh redirected URL/request_token.")
            print("Login URL:")
            print(login_url)
            pasted_value = input("Paste fresh request_token OR full redirected URL here: ")
            request_token = _extract_request_token(pasted_value)

    kite.set_access_token(data["access_token"])
    save_access_token(data["access_token"])
    print("Login successful!")
    profile = _kite_call_with_retries("Kite profile fetch", kite.profile)
    print(f"Welcome: {profile['user_name']}")

    return kite


if __name__ == "__main__":
    login()
