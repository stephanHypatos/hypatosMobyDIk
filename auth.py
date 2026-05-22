import requests
from requests.auth import HTTPBasicAuth


class HypatosAPI:
    """
    Handles authentication with the Hypatos API using OAuth 2.0 Client Credentials Grant.
    """

    def __init__(self, client_id: str, client_secret: str, base_url: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.access_token = None
        self.token_type = None
        self.expires_in = None
        self.scopes = []
        self.last_error = None

    def authenticate(self) -> bool:
        token_url = f"{self.base_url}/auth/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {"grant_type": "client_credentials"}

        try:
            response = requests.post(
                token_url,
                headers=headers,
                data=data,
                auth=HTTPBasicAuth(self.client_id, self.client_secret),
            )
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data.get("access_token")
            self.token_type = token_data.get("token_type")
            self.expires_in = token_data.get("expires_in")
            scopes_str = token_data.get("scope", "")
            self.scopes = scopes_str.split() if scopes_str else []
            self.last_error = None
            return True
        except requests.HTTPError as http_err:
            self.last_error = f"HTTP {http_err.response.status_code}: {http_err.response.text if http_err.response.text else str(http_err)}"
        except requests.ConnectionError:
            self.last_error = "Connection error: Unable to reach the API server. Please check the API URL."
        except requests.Timeout:
            self.last_error = "Timeout error: The API server took too long to respond."
        except Exception as err:
            self.last_error = str(err)
        return False

    def get_headers(self) -> dict:
        if not self.access_token or not self.token_type:
            raise ValueError("Authentication is required before making API requests.")
        return {"Authorization": f"{self.token_type} {self.access_token}"}

    def get_projects(self):
        projects_url = f"{self.base_url}/projects"
        headers = self.get_headers()
        limit = 50
        offset = 0
        all_projects = []

        try:
            while True:
                params = {"limit": limit, "offset": offset}
                response = requests.get(projects_url, headers=headers, params=params)
                response.raise_for_status()
                res_json = response.json()
                batch = res_json.get("data", [])
                total_count = res_json.get("totalCount", len(batch))
                all_projects.extend(batch)
                if len(all_projects) >= total_count:
                    break
                offset += limit
            return {"data": all_projects, "totalCount": len(all_projects)}
        except requests.HTTPError as http_err:
            self.last_error = str(http_err)
        except Exception as err:
            self.last_error = str(err)
        return None

    def get_project_schema(self, project_id: str):
        schema_url = f"{self.base_url}/projects/{project_id}/schema"
        try:
            response = requests.get(schema_url, headers=self.get_headers())
            response.raise_for_status()
            return response.json()
        except Exception as err:
            self.last_error = str(err)
        return None
