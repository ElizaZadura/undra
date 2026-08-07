import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.guardrails import check_query_guardrails


class TestAppGuardrails(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    # -- 1. Local Guardrail Unit Tests ------------------------------------- #

    def test_immigration_refusal_triggers(self):
        queries = [
            "How do I apply for a student visa to Sweden?",
            "Can you help me with Migrationsverket?",
            "What is the status of my residence permit?",
            "I need a work permit",
            "Can I travel to Lund with a Schengen visa?"
        ]
        for query in queries:
            with self.subTest(query=query):
                res = check_query_guardrails(query)
                self.assertIsNotNone(res)
                self.assertTrue(res["refused"])
                self.assertEqual(res["category"], "immigration")
                self.assertEqual(res["authority"], "Migrationsverket")
                self.assertEqual(res["routing"][0]["name"], "Migrationsverket (Swedish Migration Agency)")
                self.assertEqual(res["routing"][0]["url"], "https://www.migrationsverket.se")

    def test_tax_refusal_triggers(self):
        queries = [
            "How do I get a personnummer from Skatteverket?",
            "I need help with my Swedish taxes",
            "Where do I register for folkbokföring?",
            "What is a coordination number samordningsnummer?",
            "Do international students pay income tax?"
        ]
        for query in queries:
            with self.subTest(query=query):
                res = check_query_guardrails(query)
                self.assertIsNotNone(res)
                self.assertTrue(res["refused"])
                self.assertEqual(res["category"], "tax")
                self.assertEqual(res["authority"], "Skatteverket")
                self.assertEqual(res["routing"][0]["name"], "Skatteverket (Swedish Tax Agency)")
                self.assertEqual(res["routing"][0]["url"], "https://www.skatteverket.se")

    def test_legal_refusal_triggers(self):
        queries = [
            "My landlord refuses to return my security deposit dispute",
            "How do I handle a lease dispute with a private landlord?",
            "I have a contract dispute over my room rental",
            "Who can help me sue my landlord?",
            "Can Hyresgästföreningen help with a rental contract dispute?"
        ]
        for query in queries:
            with self.subTest(query=query):
                res = check_query_guardrails(query)
                self.assertIsNotNone(res)
                self.assertTrue(res["refused"])
                self.assertEqual(res["category"], "legal")
                self.assertIn("AF Bostäder", res["authority"])
                self.assertEqual(res["routing"][0]["name"], "AF Bostäder")
                self.assertEqual(res["routing"][0]["url"], "https://www.afbostader.se")

    def test_medical_refusal_triggers(self):
        queries = [
            "I am sick and have a fever, where is the hospital?",
            "I need a prescription for medical treatment",
            "Who do I contact at 1177 Vårdguiden?",
            "I had an accident, is there a doctor nearby?",
            "There is a safety emergency, call the police"
        ]
        for query in queries:
            with self.subTest(query=query):
                res = check_query_guardrails(query)
                self.assertIsNotNone(res)
                self.assertTrue(res["refused"])
                self.assertEqual(res["category"], "medical_safety")
                self.assertIn("1177", res["authority"])
                self.assertEqual(res["routing"][0]["name"], "1177 Vårdguiden")
                self.assertEqual(res["routing"][0]["url"], "https://www.1177.se")

    def test_medical_refusal_fever_sick_query(self):
        query = "I feel sick and have a fever"
        res = check_query_guardrails(query)
        self.assertIsNotNone(res)
        self.assertTrue(res["refused"])
        self.assertEqual(res["category"], "medical_safety")
        self.assertIn("1177", res["authority"])
        self.assertEqual(res["routing"][0]["name"], "1177 Vårdguiden")
        self.assertEqual(res["routing"][0]["url"], "https://www.1177.se")

    def test_safe_queries_do_not_refuse(self):
        queries = [
            "How do I book a laundry room tvättstuga?",
            "Where do I buy Skånetrafiken train tickets?",
            "Tell me how the Pant system works for grocery cash back.",
            "How do I sort organic food waste matavfall in Lund?",
            "What happens on Arrival Day at Lund University?",
            "How to avoid housing scams when looking for a private room?"
        ]
        for query in queries:
            with self.subTest(query=query):
                res = check_query_guardrails(query)
                self.assertIsNone(res)

    # -- 2. API Endpoint / Integration Tests ------------------------------- #

    def test_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("api_key_configured", data)

    def test_chat_endpoint_refusal_integration(self):
        response = self.client.post("/api/chat", data={"message": "Can I get a visa from Migrationsverket?"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["refused"])
        self.assertEqual(data["category"], "immigration")
        self.assertIn("I cannot advise on immigration", data["message"])

    @patch("app.main.get_gemini_client")
    def test_chat_endpoint_allowed_query_with_mock_gemini(self, mock_get_client):
        # Set up mock Gemini response
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Here is how you book a tvättstuga laundry slot using your digital key tag."
        mock_client.models.generate_content.return_value = mock_response
        mock_get_client.return_value = mock_client

        response = self.client.post("/api/chat", data={"message": "How do I book laundry?"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["refused"])
        # Ensure the EU AI Act transparency disclosure is prepended
        self.assertIn("🤖 [AI-Generated Response by Undra Assistant]", data["message"])
        self.assertIn("laundry slot", data["message"])

    def test_chat_endpoint_empty_input_fails(self):
        response = self.client.post("/api/chat", data={})
        self.assertEqual(response.status_code, 400)
