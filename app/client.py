from httpx import Client


class CodeShareClient:
    BASE_URL = "https://codeshare.frida.re/"

    def __init__(self):
        self.client = Client(base_url=self.BASE_URL, timeout=10.0)

    # https://codeshare.frida.re/search/?query=root
    def query(self, endpoint: str):
        response = self.client.get(endpoint)
        return response

    # https://codeshare.frida.re/@dzonerzy/fridantiroot/
    # https://codeshare.frida.re/@akabe1/
    def get_item(self, username: str, project_name: str | None = None):
        endpoint = f"@{username}/"
        if project_name:
            endpoint += f"{project_name}/"
        response = self.client.get(endpoint)
        return response
