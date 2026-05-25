#!/bin/bash
# LocalStack init script: runs after LocalStack is ready.
# Creates the S3 bucket used by the application.

set -e

BUCKET="${S3_BUCKET:-document-search}"
REGION="${DEFAULT_REGION:-us-east-1}"

echo "Creating S3 bucket: $BUCKET in region $REGION"
awslocal s3 mb "s3://$BUCKET" --region "$REGION" || echo "Bucket already exists — skipping."

echo "LocalStack S3 init complete."
