"""Object Storage (S3) utilities for Railway."""

import json
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
        # Upload file (Tigris objects are public by default, no ACL needed)
        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=unique_name,
            Body=file_data,
            ContentType=content_type
        )

        # Construct public URL
        # Tigris/Railway uses virtual-hosted style: https://bucket-name.endpoint/key
        endpoint = S3_ENDPOINT_URL.replace('https://', '').replace('http://', '')
        public_url = f"https://{S3_BUCKET_NAME}.{endpoint}/{unique_name}"
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

def ensure_bucket_public():
    """Ensure bucket policy allows public read access."""
    if not S3_BUCKET_NAME:
        return False

    s3 = get_s3_client()
    if not s3:
        return False

    try:
        # Set bucket policy to allow public read
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

        s3.put_bucket_policy(
            Bucket=S3_BUCKET_NAME,
            Policy=json.dumps(policy)
        )
        logger.info(f"Bucket policy updated for {S3_BUCKET_NAME}")
        return True
    except Exception as e:
        logger.warning(f"Could not set bucket policy: {e}")
        return False

def get_presigned_url(file_url, expiration=3600):
    """Generate a presigned URL for temporary access to a private object.

    Args:
        file_url: Full public URL of the file
        expiration: URL expiration time in seconds (default: 1 hour)

    Returns:
        Presigned URL that provides temporary access
    """
    if not S3_BUCKET_NAME or not file_url:
        return file_url

    s3 = get_s3_client()
    if not s3:
        return file_url

    try:
        # Extract key from URL
        prefix = f"{S3_ENDPOINT_URL}/{S3_BUCKET_NAME}/"
        if file_url.startswith(prefix):
            key = file_url[len(prefix):]
        else:
            key = file_url.split(f"/{S3_BUCKET_NAME}/")[-1]

        # Generate presigned URL
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET_NAME, 'Key': key},
            ExpiresIn=expiration
        )
        return url
    except Exception as e:
        logger.error(f"Error generating presigned URL: {e}")
        return file_url

def make_bucket_public():
    """Make all existing objects public using put_object_acl."""
    if not S3_BUCKET_NAME:
        logger.error("S3_BUCKET_NAME not configured")
        return False

    s3 = get_s3_client()
    if not s3:
        return False

    try:
        # List all objects and make them public
        response = s3.list_objects_v2(Bucket=S3_BUCKET_NAME)
        count = 0
        if 'Contents' in response:
            for obj in response['Contents']:
                key = obj['Key']
                try:
                    # Try to set ACL directly
                    s3.put_object_acl(
                        Bucket=S3_BUCKET_NAME,
                        Key=key,
                        ACL='public-read'
                    )
                    logger.info(f"Made public: {key}")
                    count += 1
                except Exception as acl_err:
                    logger.warning(f"Could not set ACL for {key}: {acl_err}")
        logger.info(f"Made {count} objects public in {S3_BUCKET_NAME}")
        return True
    except Exception as e:
        logger.error(f"Error making objects public: {e}")
        return False
