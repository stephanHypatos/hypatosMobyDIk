import requests
from auth import HypatosAPI


class HypatosDocumentClient(HypatosAPI):
    """
    Extends HypatosAPI with document fetching capabilities.
    """

    def get_documents(
        self,
        project_ids: list[str] | None = None,
        state: list[str] | None = None,
        limit: int = 50,
        max_pages: int = 100,
    ) -> list[dict]:
        """
        Fetches all documents using pagination.
        Returns a flat list of document objects.
        """
        url = f"{self.base_url}/documents"
        headers = self.get_headers()
        offset = 0
        all_docs: list[dict] = []

        while True:
            params: dict = {"limit": limit, "offset": offset}
            if project_ids:
                params["projectId"] = project_ids
            if state:
                params["state"] = state

            try:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
            except requests.HTTPError as err:
                self.last_error = f"HTTP {err.response.status_code}: {err.response.text}"
                break
            except Exception as err:
                self.last_error = str(err)
                break

            batch = data.get("data", [])
            total_count = data.get("totalCount", 0)
            all_docs.extend(batch)

            if len(all_docs) >= total_count or not batch:
                break

            offset += limit
            if offset // limit >= max_pages:
                break

        return all_docs

    def get_document_by_id(self, document_id: str) -> dict | None:
        """
        Fetches a single document by its ID including full entity data.
        """
        url = f"{self.base_url}/documents/{document_id}"
        try:
            response = requests.get(url, headers=self.get_headers())
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as err:
            self.last_error = f"HTTP {err.response.status_code}: {err.response.text}"
        except Exception as err:
            self.last_error = str(err)
        return None

    def get_sample_entity_fields(self, project_ids: list[str] | None = None) -> list[str]:
        """
        Fetches a small batch of documents and returns all unique entity field keys found.
        Used to populate the field mapping UI.
        """
        docs = self.get_documents(project_ids=project_ids, limit=10, max_pages=1)
        fields: set[str] = set()
        for doc in docs:
            entities = doc.get("entities", {})
            if isinstance(entities, dict):
                for key, value in entities.items():
                    fields.add(key)
                    # Also expose nested line-item fields if present
                    if isinstance(value, list) and value and isinstance(value[0], dict):
                        for sub_key in value[0].keys():
                            fields.add(f"{key}.{sub_key}")
        return sorted(fields)
