"""
PKCE (Proof Key for Code Exchange) utilities for OAuth 2.0.

RFC 7636: https://datatracker.ietf.org/doc/html/rfc7636
"""

import hashlib
import base64
import secrets


def generate_pkce() -> tuple[str, str]:
    """
    Generate PKCE verifier and challenge.
    
    Returns:
        (verifier, challenge) tuple
    """
    # Generate 32 bytes of randomness for the verifier
    verifier_bytes = secrets.token_bytes(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).decode().rstrip("=")
    
    # Create SHA256 challenge from verifier
    challenge_bytes = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(challenge_bytes).decode().rstrip("=")
    
    return verifier, challenge


def generate_state() -> str:
    """Generate a random state parameter for OAuth."""
    state_bytes = secrets.token_bytes(32)
    return base64.urlsafe_b64encode(state_bytes).decode().rstrip("=")


def generate_random_string(length: int = 43) -> str:
    """Generate a random string for OAuth parameters."""
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    return "".join(secrets.choice(chars) for _ in range(length))
