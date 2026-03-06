# Flask API Example

Demonstrates bigfoot's HTTP and logging plugins together.

The application module (`app.py`) makes an HTTP POST to a payment provider
and logs the result. The test (`test_app.py`) uses `bigfoot.http` to mock
the outbound HTTP call and `bigfoot.log_mock` to verify the log message.

Run: `python -m pytest examples/flask_api/ -v`
