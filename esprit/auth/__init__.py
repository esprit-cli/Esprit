"""
Esprit Authentication Module

Handles user authentication for the Esprit CLI, including:
- OAuth login flow via browser
- Credential storage and retrieval
- Token refresh
- User profile management
"""

from esprit.auth.credentials import (
    clear_credentials,
    get_auth_token,
    get_credentials,
    is_authenticated,
    save_credentials,
)

__all__ = [
    "is_authenticated",
    "get_auth_token",
    "get_credentials",
    "save_credentials",
    "clear_credentials",
]
