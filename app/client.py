DEFAULT_TTL = 3600
DEFAULT_CACHE_DIR = Path(tempfile.gettempdir()) / "frida-codeshare-mcp"
MAX_MEMORY_ENTRIES = 64  # pages are up to ~100KB each; keep the cache bounded
PRUNE_INTERVAL = 300  # seconds between disk-cache sweeps



class CodeShareClient:
    BASE_URL = "https://codeshare.frida.re/"

    def __init__(self):
        self.client = Client(base_url=self.BASE_URL, timeout=10.0)

    # https://codeshare.frida.re/search/?query=root
    def query(self, endpoint: str, params: dict[str, str] | None = None):
        response = self.client.get(endpoint, params=params)
        return response

    # https://codeshare.frida.re/@dzonerzy/fridantiroot/
    # https://codeshare.frida.re/@akabe1/
    def get_item(self, username: str, project_name: str | None = None):
        endpoint = f"@{username}/"
        if project_name:
            endpoint += f"{project_name}/"
        response = self.client.get(endpoint)
        return response


_client_ = CodeShareClient()
