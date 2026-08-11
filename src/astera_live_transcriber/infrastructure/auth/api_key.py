class ApiKeyAuthenticator:
    def __init__(self, expected_key: str) -> None:
        self._expected_key = expected_key

    def authenticate(self, provided_key: str | None) -> bool:
        return bool(provided_key) and provided_key == self._expected_key

