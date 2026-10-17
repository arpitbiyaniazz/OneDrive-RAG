from app.core.config import settings
from app.services.connectors.base import (
    Connector,
    ConnectorAuthError,
    ConnectorError,
    DriveItem,
    FileTooLargeError,
    NotFoundError,
    RootFolderMissingError,
    walk_files,
)
from app.services.connectors.google_drive import GoogleDriveConnector
from app.services.connectors.onedrive import OneDriveConnector
from app.services.connectors.sandbox import sandbox_gdrive, sandbox_onedrive

DRIVE_TYPES = ("onedrive", "google_drive")
PROVIDER_FOR_DRIVE = {"onedrive": "microsoft", "google_drive": "google"}

_onedrive = OneDriveConnector()
_gdrive = GoogleDriveConnector()


def is_sandbox(drive_type: str) -> bool:
    return settings.DEV_MOCK_ONEDRIVE if drive_type == "onedrive" else settings.DEV_MOCK_GDRIVE


def get_connector(drive_type: str) -> Connector:
    if drive_type == "onedrive":
        return sandbox_onedrive if settings.DEV_MOCK_ONEDRIVE else _onedrive
    if drive_type == "google_drive":
        return sandbox_gdrive if settings.DEV_MOCK_GDRIVE else _gdrive
    raise ValueError(f"Unknown drive type: {drive_type}")


__all__ = [
    "Connector",
    "ConnectorAuthError",
    "ConnectorError",
    "DRIVE_TYPES",
    "DriveItem",
    "FileTooLargeError",
    "NotFoundError",
    "PROVIDER_FOR_DRIVE",
    "RootFolderMissingError",
    "get_connector",
    "is_sandbox",
    "walk_files",
]
