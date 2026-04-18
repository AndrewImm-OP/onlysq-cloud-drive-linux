from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

from .config import AppConfig


class CloudError(RuntimeError):
    pass


@dataclass(slots=True)
class UploadedFile:
    remote_id: str
    public_url: str
    owner_key: str


class CloudClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "onlysq-drive/1.0.0"})

    def upload(self, local_path: Path) -> UploadedFile:
        with local_path.open("rb") as handle:
            files = {
                "file": (local_path.name, handle, "application/octet-stream")
            }
            response = self.session.post(
                self.config.upload_url,
                files=files,
                timeout=self.config.request_timeout,
            )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise CloudError(f"Upload failed: {payload}")
        public_url = str(payload["url"])
        remote_id = self.extract_remote_id(public_url)
        owner_key = str(payload["owner"])
        return UploadedFile(remote_id=remote_id, public_url=public_url, owner_key=owner_key)

    def download(self, remote_id: str, dest_path: Path) -> None:
        url = f"{self.config.file_base_url.rstrip('/')}/{remote_id}"
        response = self.session.get(url, stream=True, timeout=self.config.request_timeout)
        response.raise_for_status()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with dest_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=self.config.chunk_size):
                if chunk:
                    handle.write(chunk)

    def delete(self, remote_id: str, owner_key: str) -> None:
        if not remote_id or not owner_key:
            return
        header_name = self.config.delete_auth_header or "Authorization"
        headers = {header_name: owner_key}
        method = self.config.delete_method.upper().strip()
        if method == "GET":
            url = f"https://cloud.onlysq.ru/delete/{remote_id}"
            response = self.session.get(url, headers=headers, timeout=self.config.request_timeout)
        else:
            url = f"{self.config.delete_base_url.rstrip('/')}/{remote_id}"
            response = self.session.delete(url, headers=headers, timeout=self.config.request_timeout)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise CloudError(f"Delete failed: {payload}")

    @staticmethod
    def extract_remote_id(public_url: str) -> str:
        path = urlparse(public_url).path.rstrip("/")
        remote_id = path.rsplit("/", 1)[-1]
        if not remote_id:
            raise CloudError(f"Could not parse remote id from URL: {public_url}")
        return remote_id
