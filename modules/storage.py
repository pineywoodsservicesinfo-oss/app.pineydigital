"""Object Storage (S3) utilities for Railway."""

import os
import uuid
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import logging

logger = logging.getLogger(__name__)

# S3 Configuration from environment
S3_BUCKET_NAME = os.environ.get('S3_BUCKET_NAME')
S3_ENDPOINT_URL = os.environ.get('S3_ENDPOINT_URL')
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
S3_REGION = os.environ.get('S3_REGION', 'auto')

def get_s3_client():
    """Get S3 client configured for Railway Object Storage."""
    if not all([S3_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY]):
        logger.error("S3 credentials not configured. Check environment variables.")
        return None

    return boto3.client(
        's3',
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=S3_REGION,
        config=Config(signature_version='s3v4')
    )

def upload_file(file_data, filename, content_type='application/octet-stream', folder='photos'):
    """Upload a file to S3 and return the public URL.

    Args:
        file_data: Bytes of the file to upload
        filename: Original filename (used for extension)
        content_type: MIME type of the file
        folder: Folder path in bucket (e.g., 'photos', 'docs')

    Returns:
        Public URL of the uploaded file, or None if upload failed
    """
    if not S3_BUCKET_NAME:
        logger.error("S3_BUCKET_NAME not configured")
        return None

    s3 = get_s3_client()
    if not s3:
        return None

    # Generate unique filename
    file_ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    unique_name = f"{folder}/{uuid.uuid4().hex[:16]}.{file_ext}" if file_ext else f"{folder}/{uuid.uuid4().hex[:16]}"

    try:
        # Upload without ACL - Railway uses public buckets by default
        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=unique_name,
            Body=file_data,
            ContentType=content_type
        )

        # Construct public URL
        # Railway uses path-style URLs: https://endpoint/bucket-name/key
        public_url = f"{S3_ENDPOINT_URL}/{S3_BUCKET_NAME}/{unique_name}"
        logger.info(f"File uploaded successfully: {public_url}")
        return public_url

    except ClientError as e:
        logger.error(f"S3 upload error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error uploading file: {e}")
        return None

def delete_file(file_url):
    """Delete a file from S3 by its URL.

    Args:
        file_url: Full public URL of the file

    Returns:
        True if deleted, False otherwise
    """
    if not S3_BUCKET_NAME or not file_url:
        return False

    s3 = get_s3_client()
    if not s3:
        return False

    try:
        # Extract key from URL
        prefix = f"{S3_ENDPOINT_URL}/{S3_BUCKET_NAME}/"
        if file_url.startswith(prefix):
            key = file_url[len(prefix):]
        else:
            # Try alternative parsing
            key = file_url.split(f"/{S3_BUCKET_NAME}/")[-1]

        s3.delete_object(Bucket=S3_BUCKET_NAME, Key=key)
        logger.info(f"File deleted: {key}")
        return True

    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        return False

def is_configured():
    """Check if S3 storage is properly configured."""
    return all([S3_BUCKET_NAME, S3_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY])

def make_bucket_public():
    """Set bucket policy to allow public read access."""
    if not S3_BUCKET_NAME:
        logger.error("S3_BUCKET_NAME not configured")
        return False

    s3 = get_s3_client()
    if not s3:
        return False

    try:
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "PublicReadGetObject",
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{S3_BUCKET_NAME}/*"
                }
            ]
        }
        import json
        s3.put_bucket_policy(Bucket=S3_BUCKET_NAME, Policy=json.dumps(policy))
        logger.info(f"Bucket {S3_BUCKET_NAME} is now public")
        return True
    except Exception as e:
        logger.error(f"Error setting bucket policy: {e}")
        return False
