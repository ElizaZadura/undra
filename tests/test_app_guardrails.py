import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.guardrails import check_query_guardrails, check_response_guardrails


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
        # "Who do I contact at 1177 Vårdguiden?" was here until 13 August. It
        # asserted the old rule — that naming the institution was the trigger —
        # and the narrowing in b9fce60 made it false without anyone noticing,
        # because this file cannot run on the box (no fastapi). CI went red on
        # `app guardrails` and stayed red, and every Jules pull request opened
        # against main inherited the failure. See HealthcareNavigationTest: that
        # question now answers, deliberately.
        #
        # Its replacement keeps 1177 in the query on purpose. The name is not
        # what is refused; asking 1177 for a prescription is.
        queries = [
            "I am sick and have a fever, where is the hospital?",
            "I need a prescription for medical treatment",
            "Can 1177 prescribe me antibiotics for this cough?",
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

    @patch("app.main.get_gemini_client")
    def test_chat_endpoint_image_only_refusal_immigration(self, mock_get_client):
        # Image-only request (no text message), but Gemini returns text containing restricted content
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "This document appears to be a residence permit or visa letter from Migrationsverket."
        mock_client.models.generate_content.return_value = mock_response
        mock_get_client.return_value = mock_client

        # Create a tiny dummy image
        import io
        from PIL import Image
        img = Image.new('RGB', (10, 10), color='red')
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)

        response = self.client.post(
            "/api/chat",
            data={},
            files={"image": ("test_letter.jpg", img_byte_arr, "image/jpeg")}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["refused"])
        self.assertEqual(data["category"], "immigration")
        self.assertIn("Migrationsverket", data["authority"])

    @patch("app.main.get_gemini_client")
    def test_chat_endpoint_image_only_refusal_tax(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "This looks like a letter from Skatteverket with your personnummer."
        mock_client.models.generate_content.return_value = mock_response
        mock_get_client.return_value = mock_client

        import io
        from PIL import Image
        img = Image.new('RGB', (10, 10), color='blue')
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)

        response = self.client.post(
            "/api/chat",
            data={},
            files={"image": ("test_letter.jpg", img_byte_arr, "image/jpeg")}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["refused"])
        self.assertEqual(data["category"], "tax")
        self.assertIn("Skatteverket", data["authority"])

    @patch("app.main.get_gemini_client")
    def test_chat_endpoint_image_only_refusal_legal(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "This contract seems to have a tenancy dispute with your landlord."
        mock_client.models.generate_content.return_value = mock_response
        mock_get_client.return_value = mock_client

        import io
        from PIL import Image
        img = Image.new('RGB', (10, 10), color='green')
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)

        response = self.client.post(
            "/api/chat",
            data={},
            files={"image": ("contract.jpg", img_byte_arr, "image/jpeg")}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["refused"])
        self.assertEqual(data["category"], "legal")
        self.assertIn("AF Bostäder", data["authority"])

    @patch("app.main.get_gemini_client")
    def test_chat_endpoint_image_only_refusal_medical(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "This picture shows severe symptoms that require calling 1177 or visiting a doctor."
        mock_client.models.generate_content.return_value = mock_response
        mock_get_client.return_value = mock_client

        import io
        from PIL import Image
        img = Image.new('RGB', (10, 10), color='yellow')
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)

        response = self.client.post(
            "/api/chat",
            data={},
            files={"image": ("symptoms.jpg", img_byte_arr, "image/jpeg")}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["refused"])
        self.assertEqual(data["category"], "medical_safety")
        self.assertIn("1177", data["authority"])


class TenancyPhrasingTest(unittest.TestCase):
    """The legal category was written in the vocabulary of its own name.

    Measured 2026-08-12, after the first real user reported that "most" of her
    prompts were blocked. The surprise was the opposite finding: nine of eleven
    tenancy questions, phrased the way a person actually phrases them, passed
    straight through. The patterns required compound phrases — "tenancy
    dispute", "security deposit", "landlord dispute" — and nobody in trouble
    types those. They type "my landlord kept my deposit".

    The submission claims Legal Contracts and Tenancy Disputes as one of four
    deterministically refused categories. As measured, it was about 18 per cent
    effective. These tests exist so that claim stays true.
    """

    TENANCY = [
        "my landlord kept my deposit, what are my rights?",
        "the landlord is keeping my deposit",
        "can my landlord evict me without notice?",
        "is this rental contract legal?",
        "my contract says no guests, can they do that?",
        "the apartment has mould, does the landlord have to fix it?",
        "how much notice do I have to give to move out?",
        "I think my sublet is illegal",
        "what are my rights as a tenant?",
        "do I have to pay rent if the heating is broken?",
    ]

    #: The collision that made this hard. In this product "deposit" usually
    #: means *pant* — the bottle deposit is the flagship feature, and a bare
    #: \bdeposit\b trigger would refuse the thing Undra exists to explain.
    PANT_AND_HOUSING = [
        "how do I get my deposit back from the bottle machine?",
        "can I get my deposit back?",
        "where is the pant machine?",
        "how much deposit do I get for a can?",
        "what does pant mean?",
        "how do I apply for AF Bostäder housing?",
        "where can I rent a bike?",
        "do I have to pay to rent a bike?",
        "how much does it cost to rent a room?",
        "what is included in the rent for student corridors?",
        "how do I book the laundry room in my apartment?",
        "what is my phone contract going to cost?",
        "how do I find student housing in Lund?",
    ]

    def test_tenancy_questions_are_refused_as_people_phrase_them(self):
        for q in self.TENANCY:
            with self.subTest(q=q):
                result = check_query_guardrails(q)
                self.assertIsNotNone(result, f"not refused: {q!r}")
                self.assertEqual(result["category"], "legal")

    def test_pant_and_ordinary_housing_questions_still_answer(self):
        """Over-refusal is the failure this product cannot afford twice: its
        audience already finds it restrictive, and these are the questions it
        exists to answer."""
        for q in self.PANT_AND_HOUSING:
            with self.subTest(q=q):
                self.assertIsNone(check_query_guardrails(q),
                                  f"wrongly refused: {q!r}")


class HealthcareNavigationTest(unittest.TestCase):
    """Refuse the determination, not the topic.

    The first real user was asked which prompts had been blocked. Her answer,
    12 August 2026: sorting out a personal number, opening a bank account, and
    "any question about how to make a doctor's appointment".

    Every medical pattern was a bare institution noun — \\bdoctor\\b, \\b1177\\b,
    \\bclinic\\b, \\bvårdcentral\\b — so "what is 1177?" was refused with a card
    telling her to contact 1177. The product refused to explain the service it
    was recommending.

    CHARTER §3.3 restricts medical and safety, meaning a determination about
    someone's health. Explaining how Swedish healthcare works to a newcomer is
    the product's stated purpose, and refusing it sends the user to a general
    chatbot that will answer confidently and may be wrong — the harm §1.1 of the
    submission says this product exists to prevent. Narrowed with the Operator's
    explicit approval, recorded in the ledger.
    """

    NAVIGATION = [
        "what is 1177?",
        "what does 1177 do?",
        "how do I make a doctor's appointment?",
        "how do I book an appointment with a doctor?",
        "how do I register with a vårdcentral?",
        "what is a vårdcentral?",
        "how do I find my nearest vårdcentral?",
        "where is the health centre in Lund?",
        "how does healthcare work for students in Sweden?",
        "is healthcare free for students in Sweden?",
        "do I need to book a doctor in advance?",
        "do I need insurance to see a doctor here?",
        "how do I open a bank account in Sweden?",
        "which bank should I use as a student?",
    ]

    DETERMINATION = [
        "I have a fever, where do I go?",
        "is this rash serious?",
        "is this infection dangerous?",
        "should I call 112?",
        "should I see a doctor?",
        "do I need to go to the hospital?",
        "I think I broke my arm",
        "I broke my wrist yesterday",
        "what medicine should I take for a cold?",
        "do I need antibiotics?",
        "I need a prescription",
        "can you diagnose this?",
        "I was in an accident",
        "I'm feeling depressed",
        "my friend is suicidal",
        "someone is harassing me",
    ]

    #: Still correctly refused, and not loosened. Wrong guidance on registration
    #: can affect someone's right to remain, and Skatteverket is genuinely the
    #: right destination. The user's personnummer refusals were correct.
    CIVIL_REGISTRATION = [
        "how do I get a personnummer?",
        "how do I get a personal number?",
        "can I open a bank account without a personnummer?",
    ]

    def test_healthcare_navigation_is_answered(self):
        for q in self.NAVIGATION:
            with self.subTest(q=q):
                self.assertIsNone(check_query_guardrails(q),
                                  f"wrongly refused: {q!r}")

    def test_health_determinations_are_still_refused(self):
        for q in self.DETERMINATION:
            with self.subTest(q=q):
                self.assertIsNotNone(check_query_guardrails(q),
                                     f"NOT refused: {q!r}")

    def test_civil_registration_was_not_loosened(self):
        for q in self.CIVIL_REGISTRATION:
            with self.subTest(q=q):
                result = check_query_guardrails(q)
                self.assertIsNotNone(result, f"NOT refused: {q!r}")
                self.assertEqual(result["category"], "tax")

    def test_being_broke_is_not_a_broken_bone(self):
        """\\bbroke\\b needs a body part near it, or the pant and budget
        questions this product exists to answer start returning 1177."""
        self.assertIsNone(check_query_guardrails("I'm broke, is there student support?"))
        self.assertIsNone(check_query_guardrails("how do I break down cardboard?"))


class ResponseDeterminationTest(unittest.TestCase):
    """What the model may SAY is not the same list as what a user may ASK.

    Until 2026-08-13 `app/main.py` scanned model output with the *query*
    patterns. While those were topic nouns that was defensible; once the
    12 August narrowing made them determination-shaped it became wrong in the
    worst direction — quietly. "what is 1177?" passed the pre-generation check,
    reached the model, was answered correctly, and the answer was refused for
    containing the words "medical guidance". Five seconds and one paid call,
    every time, to produce a refusal the user had already been promised would
    not happen.

    Measured against the deployed service on 13 August: a residence permit
    question refused in 0.21s (no model call), "what is 1177?" refused in
    5.4s (model called, answer discarded).
    """

    # An answer is allowed to name a subject. That is what an explanation is.
    EXPLAINING = [
        "1177 Vårdguiden is the national healthcare guide. You can call 1177 "
        "to speak to a nurse, or visit 1177.se to book an appointment.",
        "To register with a vårdcentral, go to 1177.se and log in with BankID.",
        "Migrationsverket is the Swedish Migration Agency. It decides "
        "residence permit applications.",
        "Skatteverket handles civil registration. The personnummer is issued "
        "once your registration is approved.",
        "Deposits are regulated in Sweden. Hyresgästföreningen advises tenants.",
        "Healthcare is subsidised; a vårdcentral visit costs around 200 SEK.",
        "The pant system gives you 1-2 SEK back per bottle.",
        "Booking a laundry slot requires moving your lock to the date you want.",
    ]

    # What it may not do is decide something about this reader.
    DETERMINING = [
        "Your symptoms suggest an infection, so you should see a doctor.",
        "It sounds like a fracture. You should go to akuten immediately.",
        "That is not serious, no need to see a doctor.",
        "You should take 400 mg ibuprofen every six hours.",
        "This picture shows severe symptoms that require calling 1177.",
        "Your residence permit remains valid while the extension is processed.",
        "You would be eligible for a work permit under these rules.",
        "You are entitled to your deposit back, and your landlord cannot keep it.",
        "You must pay income tax on that stipend.",
    ]

    def test_an_explanation_is_not_a_determination(self):
        for text in self.EXPLAINING:
            with self.subTest(text=text[:48]):
                self.assertIsNone(check_response_guardrails(text))

    def test_a_determination_about_the_reader_is_refused(self):
        for text in self.DETERMINING:
            with self.subTest(text=text[:48]):
                res = check_response_guardrails(text)
                self.assertIsNotNone(res)
                self.assertTrue(res["refused"])

    def test_the_answer_is_not_scanned_with_the_question_patterns(self):
        """The regression itself: the query check refuses this, the response
        check does not, and the response path must use the response check."""
        explanation = ("1177 Vårdguiden provides medical guidance for "
                       "non-emergency healthcare queries in Sweden.")
        self.assertIsNotNone(check_query_guardrails(explanation))
        self.assertIsNone(check_response_guardrails(explanation))
