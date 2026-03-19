"""S3 upload with SQS notification."""

import boto3


def upload_and_notify(bucket, key, body, queue_url):
    """Upload a file to S3 and send a notification to SQS."""
    s3 = boto3.client("s3", region_name="us-east-1")
    sqs = boto3.client("sqs", region_name="us-east-1")
    s3.put_object(Bucket=bucket, Key=key, Body=body)
    sqs.send_message(QueueUrl=queue_url, MessageBody=f"Uploaded {key}")
