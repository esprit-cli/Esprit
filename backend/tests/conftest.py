"""Shared backend test configuration."""

import os

# Required settings for app config during imports.
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "aaa.bbb.ccc")
os.environ.setdefault("AWS_REGION", "us-east-1")
