import re
from typing import Dict, Any, List, Optional

# Define patterns and keywords for each restricted category
REFUSAL_CATEGORIES = {
    "immigration": {
        "title": "Immigration & Visas",
        "authority": "Migrationsverket",
        "patterns": [
            r"\bmigrationsverket\b",
            r"\bmigration agency\b",
            r"\bmigration board\b",
            r"\bvisa\b",
            r"\bvisas\b",
            r"\bresidence permit\b",
            r"\bresidency permit\b",
            r"\bwork permit\b",
            r"\buppehållstillstånd\b",
            r"\bcitizenship\b",
            r"\bpassport\b",
            r"\basylum\b",
            r"\bdeport\b",
            r"\bvisa status\b",
            r"\bschengen\b"
        ],
        "message": (
            "I cannot advise on immigration, visa, or residence permit matters. "
            "These processes are highly legally sensitive and have direct impacts on your visa status in Sweden. "
            "Please consult the official authority directly for accurate and legally binding information."
        ),
        "routing": [
            {
                "name": "Migrationsverket (Swedish Migration Agency)",
                "url": "https://www.migrationsverket.se"
            }
        ]
    },
    "tax": {
        "title": "Taxes & Civil Registration",
        "authority": "Skatteverket",
        "patterns": [
            r"\bskatteverket\b",
            r"\btax agency\b",
            r"\btax office\b",
            r"\btax\b",
            r"\btaxes\b",
            r"\btaxation\b",
            r"\bpersonnummer\b",
            r"\bsamordningsnummer\b",
            r"\bcoordination number\b",
            r"\bpersonal identity number\b",
            r"\bpersonal number\b",
            r"\bcivil registration\b",
            r"\bfolkbokföring\b",
            r"\bfolkbokförd\b",
            r"\bdeklaration\b",
            r"\bincome tax\b"
        ],
        "message": (
            "I cannot advise on taxes, civil registration, personal identity numbers (personnummer), "
            "or coordination numbers. Please refer to Skatteverket, the official tax authority in Sweden, "
            "to register your move and obtain official guidance."
        ),
        "routing": [
            {
                "name": "Skatteverket (Swedish Tax Agency)",
                "url": "https://www.skatteverket.se"
            }
        ]
    },
    "legal": {
        "title": "Legal Contracts & Tenancy Disputes",
        "authority": "AF Bostäder & Lund University International Desk",
        "patterns": [
            r"\bcontract dispute\b",
            r"\btenancy dispute\b",
            r"\blease dispute\b",
            r"\blandlord dispute\b",
            r"\blegal contract\b",
            r"\brental contract\b",
            r"\btenancy contract\b",
            r"\bhyresgästföreningen\b",
            r"\bhyresnämnden\b",
            r"\bevict\b",
            r"\beviction\b",
            r"\bsue\b",
            r"\blegal action\b",
            r"\blegal advice\b",
            r"\blegal aid\b",
            r"\bbreach of contract\b",
            r"\bdeposit dispute\b",
            r"\bsecurity deposit\b",
            r"\btenancy agreement\b",
            r"\blegal scam\b",

            # --- co-occurrence patterns, added 2026-08-12 ------------------ #
            #
            # Everything above is a compound phrase written in the vocabulary
            # of the category name. Measured against how people actually type,
            # nine of eleven tenancy questions passed straight through:
            # "my landlord kept my deposit, what are my rights?", "what are my
            # rights as a tenant?", "the apartment has mould, does the landlord
            # have to fix it?". Nobody in trouble writes "tenancy dispute".
            #
            # These match a HOUSING term co-occurring with a RIGHTS-or-DISPUTE
            # term, in either order, anywhere in the query.
            #
            # Bare "deposit" is deliberately NOT a trigger. In this product it
            # usually means *pant* — the bottle deposit is the flagship feature,
            # and refusing "how do I get my deposit back?" would break the thing
            # Undra exists to explain. That ambiguity is left to the
            # post-generation scanner: if the model answers a bare "deposit"
            # question by talking about landlords, the same patterns catch it
            # on the way out.
            (r"(?=.*\b(landlord|hyresvärd|tenant|hyresgäst|tenancy|lease|"
             r"sublet|sublease|andrahand|inneboende|rental|apartment|flat|"
             r"corridor room|accommodation|housing contract)\b)"
             r"(?=.*\b(right|rights|entitled|obliged|obligated|liable|"
             r"allowed to|can they|legal|illegal|unlawful|breach|dispute|"
             r"complain|court|notice period|terminate|evict)\b)"),

            (r"(?=.*\b(landlord|hyresvärd|tenancy|lease|rental|sublet|"
             r"andrahand|apartment)\b)"
             r"(?=.*\b(deposit|deposits|deposition)\b)"),

            (r"(?=.*\b(landlord|hyresvärd)\b)"
             r"(?=.*\b(kept|keeping|withhold|withheld|refus|owe|owes|"
             r"has to fix|have to fix|must fix|responsible)\b)"),

            # Contract terms someone is questioning the validity of. "contract"
            # is kept out of the housing list above because it is common and
            # harmless on its own; here it must sit next to a challenge.
            (r"(?=.*\b(contract|agreement|clause)\b)"
             r"(?=.*\b(can they|are they allowed|is that legal|is this legal|"
             r"is it legal|valid|binding|enforceable|get out of|break)\b)"),

            # Notice periods and rent withholding are both regulated, and both
            # arrive phrased as practical questions rather than legal ones.
            # "pay rent" as a phrase, not \brent\b, so "pay to rent a bike"
            # does not match.
            r"\bhow (much|long|many days|many months) notice\b",
            (r"(?=.*\bpay(?:ing)?\s+(?:the\s+)?rent\b)"
             r"(?=.*\b(have to|has to|must|obliged|withhold|refuse|stop|"
             r"broken|not working|no heating|mould|repair)\b)"),
        ],
        "message": (
            "I cannot provide advice on legal contracts, tenancy agreements, lease disputes, or legal conflicts with landlords. "
            "For housing contract assistance and official accommodation guidance in Lund, please reach out to AF Bostäder "
            "or the Lund University International Desk, who support international students with lodging."
        ),
        "routing": [
            {
                "name": "AF Bostäder",
                "url": "https://www.afbostader.se"
            },
            {
                "name": "Lund University International Desk",
                "url": "https://www.lunduniversity.lu.se/student-life/preparing-come/international-desk"
            }
        ]
    },
    "medical_safety": {
        "title": "Medical & Safety Concerns",
        "authority": "1177 Vårdguiden & Emergency Services (112)",
        "patterns": [
            # Narrowed 2026-08-12, on the first real user's feedback and with
            # the Operator's explicit approval.
            #
            # Every pattern here used to be a bare institution noun — \bdoctor\b,
            # \b1177\b, \bclinic\b, \bvårdcentral\b. Naming the institution was
            # the trigger, so "what is 1177?" was refused with a card telling the
            # user to contact 1177. The product refused to explain the service it
            # was recommending.
            #
            # That is topic detection. What CHARTER §3.3 forbids is a
            # DETERMINATION about someone's health — a diagnosis, a judgement of
            # urgency, a treatment. Explaining how Swedish healthcare works for a
            # newcomer is the product's stated purpose, and refusing it sends the
            # user to a general chatbot that will answer confidently and may be
            # wrong — the exact harm the product exists to prevent. A wall is not
            # safety.
            #
            # Symptoms, diagnosis, treatment, urgency and crisis stay hard
            # refusals. Booking, registering and "what is 1177" now answer.

            # --- symptoms and conditions ---
            r"\bfever\b", r"\bpain\b", r"\bache\b", r"\bsick\b", r"\bill\b",
            r"\billness\b", r"\binjury\b", r"\binjured\b", r"\bwound\b",
            r"\bbleeding\b", r"\brash\b", r"\binfection\b", r"\bsymptom",
            r"\bnausea\b", r"\bvomit", r"\bdizzy\b", r"\ballerg",
            # "broke my arm", not just "broken arm" — the tense people use.
            # \bbroke\b alone would catch "I'm broke", so a body part must
            # follow within a few words.
            (r"\bbro(?:ke|ken)\b.{0,14}"
             r"\b(bone|arm|leg|wrist|ankle|rib|finger|toe|nose|collarbone)\b"),
            r"\bsprain",
            r"\bcough\b", r"\bflu\b", r"\bcovid\b", r"\baccident\b",

            # --- diagnosis and treatment ---
            r"\bprescribe\b", r"\bprescription\b", r"\bmedicine\b",
            r"\bmedication\b", r"\bdiagnos", r"\btreatment\b", r"\bdosage\b",
            r"\bantibiotic", r"\bpainkiller", r"\bmedical advice\b",
            r"\bhealth advice\b", r"\bwhat.{0,12}wrong with me\b",

            # --- urgency and emergency ---
            r"\b112\b", r"\bambulance\b", r"\bemergency\b", r"\bakuten\b",
            r"\burgent care\b", r"\blife.threatening\b", r"\bunconscious\b",

            # --- mental health and crisis. Deliberately still broad: the cost of
            #     a wrong call here is not symmetrical with the cost of an
            #     unhelpful refusal.
            r"\bmental health\b", r"\bdepress", r"\banxiety\b",
            r"\bsuicid", r"\bself.harm\b",

            # --- personal safety ---
            r"\bpolice\b", r"\bassault", r"\bharass", r"\bsafety issue\b",
            r"\bthreatened\b",

            # --- asking for a judgement about whether to seek care. Note the
            #     verbs: "should I SEE a doctor" is a determination; "do I need
            #     to BOOK a doctor in advance" is a question about the booking
            #     system, and answers.
            # The modal must attach to the care verb itself. An earlier version
            # allowed 24 characters of slack and so refused "do I need INSURANCE
            # to see a doctor here?" — an admin question about the healthcare
            # system, which is exactly what this narrowing exists to allow.
            (r"\b(should|shall|must|ought)\s+(i|we|he|she|they)\s+"
             r"(see|visit|go\s+to)\b.{0,16}"
             r"\b(doctor|hospital|vårdcentral|clinic|akuten)\b"),
            (r"\b(do|does|did)\s+(i|we|he|she|they)\s+(need|have)\s+to\s+"
             r"(see|visit|go\s+to)\b.{0,16}"
             r"\b(doctor|hospital|vårdcentral|clinic|akuten)\b"),
            r"\b(is|are)\b.{0,20}\b(serious|dangerous|worrying|infected)\b",
        ],
        "message": (
            "I cannot provide medical advice, diagnosis, or assist with physical or mental health and safety emergencies. "
            "For medical guidance and non-emergency healthcare queries in Sweden, please contact 1177 Vårdguiden. "
            "In case of a life-threatening emergency, call 112 immediately."
        ),
        "routing": [
            {
                "name": "1177 Vårdguiden",
                "url": "https://www.1177.se"
            },
            {
                "name": "Swedish Emergency Services (Call 112)",
                "url": "https://www.sosalarm.se"
            }
        ]
    }
}


def check_query_guardrails(text: str) -> Optional[Dict[str, Any]]:
    """
    Checks if a user's query triggers any refusal guardrail category.
    Returns a dictionary containing refusal info if triggered, or None if safe.
    """
    if not text:
        return None

    text_lower = text.lower()

    for category_id, config in REFUSAL_CATEGORIES.items():
        for pattern in config["patterns"]:
            if re.search(pattern, text_lower):
                # Match found! Return the structured refusal and routing information
                return {
                    "refused": True,
                    "category": category_id,
                    "title": config["title"],
                    "authority": config["authority"],
                    "message": config["message"],
                    "routing": config["routing"]
                }

    return None
