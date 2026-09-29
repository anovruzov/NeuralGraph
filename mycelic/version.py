"""The Mycelic release version.

Kept in its own module so that importing the package, the SDK or the test harness never loads the server stack
(aiohttp, nats-py, prometheus-client): agents and the smoke test run on the standard library alone.
"""
VERSION = "0.1.0"
