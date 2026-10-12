from unittest import mock

from django.conf import settings
from django.db import DatabaseError, connection
from rest_framework.test import APITestCase


class HealthEndpointTests(APITestCase):
    url = "/api/health/"

    def test_health_returns_ok_when_database_responds(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})

    def test_health_returns_503_without_details_when_database_fails(self):
        with mock.patch.object(connection, "cursor", side_effect=DatabaseError("boom")):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "error", "database": "unavailable"})

    def test_health_response_exposes_no_configuration(self):
        body = self.client.get(self.url).content.decode()
        db = settings.DATABASES["default"]

        for secret in (settings.SECRET_KEY, db["PASSWORD"], db["USER"], db["HOST"]):
            self.assertNotIn(secret, body)

    def test_application_database_is_postgresql(self):
        self.assertEqual(connection.vendor, "postgresql")
