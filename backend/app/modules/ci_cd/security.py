import base64
import secrets


def generate_ci_token() -> str:
    value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    return f"cit_{value}"
