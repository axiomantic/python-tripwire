"""Test boto3 S3 upload with SQS notification using bigfoot boto3_mock."""

import logging

import pytest

import bigfoot

from .app import upload_and_notify


@pytest.fixture(autouse=True)
def _silence_botocore():
    """Suppress botocore DEBUG logs that would generate dozens of LoggingPlugin interactions."""
    for name in ("botocore", "boto3", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def test_upload_and_notify():
    bigfoot.boto3_mock.mock_call("s3", "PutObject", returns={})
    bigfoot.boto3_mock.mock_call("sqs", "SendMessage", returns={"MessageId": "msg-001"})

    with bigfoot:
        upload_and_notify(
            "data-bucket", "reports/q1.csv", b"revenue,100",
            "https://sqs.us-east-1.amazonaws.com/123/notifications",
        )

    bigfoot.boto3_mock.assert_boto3_call(
        service="s3", operation="PutObject",
        params={"Bucket": "data-bucket", "Key": "reports/q1.csv", "Body": b"revenue,100"},
    )
    bigfoot.boto3_mock.assert_boto3_call(
        service="sqs", operation="SendMessage",
        params={
            "QueueUrl": "https://sqs.us-east-1.amazonaws.com/123/notifications",
            "MessageBody": "Uploaded reports/q1.csv",
        },
    )
