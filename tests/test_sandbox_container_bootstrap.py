from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_installs_aws_cli_for_cloud_uploads() -> None:
    content = (ROOT / "containers" / "Dockerfile").read_text()

    assert "awscli-exe-linux-${AWS_ARCH}.zip" in content
    assert "/tmp/aws/install" in content


def test_entrypoint_extracts_local_uploads_into_workspace() -> None:
    content = (ROOT / "containers" / "docker-entrypoint.sh").read_text()

    assert 'if [ -n "${UPLOAD_S3_KEY:-}" ] && [ -n "${S3_BUCKET:-}" ]; then' in content
    assert 'aws s3 cp "s3://${S3_BUCKET}/${UPLOAD_S3_KEY}" /tmp/upload.tar.gz' in content
    assert "tar -xzf /tmp/upload.tar.gz -C /workspace" in content
    assert 'echo "✅ Source files extracted to /workspace"' in content
