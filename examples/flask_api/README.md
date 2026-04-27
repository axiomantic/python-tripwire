# Flask API Example

Demonstrates tripwire's HTTP and logging plugins together.

The application module (`app.py`) makes an HTTP POST to a payment provider
and logs the result. The test (`test_app.py`) uses `tripwire.http` to mock
the outbound HTTP call and `tripwire.log_mock` to verify the log message.

Run: `python -m pytest examples/flask_api/ -v`
