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


# --------------------------------------------------------------------------- #
# What the model is not allowed to SAY, which is not the same list as what a
# user is not allowed to ASK.
#
# Added 2026-08-13. Until today `app/main.py` scanned the model's answer with
# the patterns above — the query patterns. That was defensible while those
# patterns were topic nouns, and became wrong the moment they stopped being: a
# correct answer to "what is 1177?" says "1177 Vårdguiden provides medical
# guidance", and "medical guidance" was enough to bin it. The pre-generation
# fix of 12 August therefore did nothing for the questions it was written for.
# They reached the model, were answered, and the answer was discarded after
# being paid for — five seconds and one call, every time.
#
# The distinction is the same one CHARTER §3.3 draws, applied on the way out
# instead of the way in. A refusal here is about the model making a
# DETERMINATION about this user — their diagnosis, their eligibility, their
# liability — and not about the model naming a subject.
#
#   refused:  "you should see a doctor about that"
#             "your residence permit remains valid"
#             "you are entitled to your deposit back"
#   allowed:  "1177 Vårdguiden is where you book non-emergency care"
#             "Migrationsverket decides residence permits"
#             "deposits are regulated; Hyresgästföreningen advises tenants"
#
# Second person throughout, deliberately. A statement about the world is the
# product's purpose; a statement about the reader is the thing it must not make.
RESPONSE_DETERMINATION_PATTERNS: Dict[str, List[str]] = {
    "medical_safety": [
        # Diagnosis aimed at the reader.
        r"\byou (probably |likely |may |might |could )?have\b.{0,30}"
        r"\b(infection|fracture|virus|flu|covid|condition|illness|disease)\b",
        r"\byour symptoms\b",
        r"\b(it|this) (sounds|looks|seems) like\b.{0,24}"
        r"\b(infection|fracture|broken|virus|flu|covid|allergic|serious)\b",
        r"\byou are (likely |probably )?(suffering|experiencing)\b",
        # Treatment and dosage.
        r"\byou should (take|apply|use)\b.{0,24}"
        r"\b(mg|ml|tablet|painkiller|antibiotic|ibuprofen|paracetamol|alvedon)\b",
        r"\btake\s+\d+\s*(mg|ml|tablets?|pills?)\b",
        r"\bi (recommend|suggest|advise) (that )?you (take|apply|use)\b",
        # Urgency, in both directions. "You do not need a doctor" is a
        # determination too, and the more dangerous of the two.
        r"\byou (should|need to|must|ought to) (see|visit|go to|call)\b.{0,24}"
        r"\b(doctor|hospital|akuten|112|emergency|vårdcentral)\b",
        r"\byou (do not|don't) (need to|have to) (see|visit|go to|call)\b.{0,24}"
        r"\b(doctor|hospital|akuten|112|emergency)\b",
        r"\b(this|that|it) is (not |probably not )?(serious|an emergency|dangerous|urgent)\b",
        r"\bno need to (worry|see a doctor|go to)\b",
        # Determinations made about an image rather than to the reader. The
        # multimodal path is the one place an assessment can arrive without the
        # user having typed anything a query pattern could read: "this picture
        # shows severe symptoms that require calling 1177" names no reader and
        # is still a clinical judgement.
        r"\bsymptoms\b.{0,40}\b(require|requires|need|needs|suggest|suggests|"
        r"indicate|indicates|consistent with)\b",
        r"\b(require|requires|needs?|needing)\s+(immediate|urgent|emergency)\b",
        r"\b(require|requires|needs?)\s+(calling|contacting)\s+"
        r"(1177|112|a doctor|emergency)\b",
    ],
    "immigration": [
        r"\byour (residence |work |student )?permit\b.{0,30}"
        r"\b(is|remains|stays|will be|would be|is not)\b.{0,20}"
        r"\b(valid|invalid|approved|rejected|granted|revoked)\b",
        r"\byou (are|would be|will be) (eligible|ineligible|entitled)\b.{0,30}"
        r"\b(permit|visa|citizenship|residence|asylum)\b",
        r"\byou (qualify|do not qualify|don't qualify) for\b.{0,30}"
        r"\b(permit|visa|citizenship|residence|asylum)\b",
        r"\byour application (will|would|should) be (approved|rejected|granted)\b",
        r"\byou (can|cannot|can't|may|may not) (stay|remain|work|enter)\b.{0,24}"
        r"\b(sweden|schengen|eu)\b",
    ],
    "tax": [
        r"\byou (are|will be|would be) (liable|taxed|required to pay)\b",
        r"\byou (must|need to|should) (pay|declare|report)\b.{0,24}"
        r"\b(tax|skatt|income tax)\b",
        r"\byou (do not|don't) (have to|need to) pay\b.{0,20}\b(tax|skatt)\b",
        r"\byou (are|are not|aren't) (considered )?(a )?(tax )?resident\b",
        r"\byour (tax|skatt)\b.{0,20}\b(rate|liability|bracket) (is|will be)\b",
    ],
    "legal": [
        r"\byou (are|would be) entitled to\b",
        r"\byou have (a |the )?(legal )?right to\b",
        r"\byour landlord (must|has to|cannot|can't|is required|is not allowed)\b",
        r"\byou (can|could|should) (sue|take .{0,20}to court|file a claim)\b",
        r"\b(that|this) (is|would be) (illegal|unlawful|a breach|not legal)\b",
        r"\byou (are not|aren't) (obliged|required|liable)\b",
        r"\byou (can|cannot|can't) (withhold|stop paying|refuse to pay)\b",
    ],
}


# Applied only when the request carried an image.
#
# The asymmetry is the reason. `check_query_guardrails` reads the user's typed
# text; it cannot read a photograph. So when someone uploads a picture of a
# Migrationsverket decision, or a Skatteverket letter with their personnummer
# on it, or their tenancy contract, nothing has examined the actual subject of
# the request by the time the model answers. The output scan is the only net
# there is, and it has to be wider than the one covering text — a summary of
# the reader's own permit decision is immigration advice whether or not it is
# phrased as a determination.
#
# Not applied to text-only answers, where it would be far too broad: "you will
# get a letter from Migrationsverket" is a fine sentence in an answer about
# arrival logistics, and the query guard has already seen the question.
IMAGE_DOCUMENT_PATTERNS: Dict[str, List[str]] = {
    "immigration": [
        r"(?=.*\b(document|letter|form|card|decision|notice|permit|application|"
        r"passport|picture|photo|image|screenshot)\b)"
        r"(?=.*\b(residence permit|uppehållstillstånd|visa|work permit|"
        r"citizenship|migrationsverket|asylum|schengen)\b)",
    ],
    "tax": [
        r"(?=.*\b(document|letter|form|card|decision|notice|statement|"
        r"picture|photo|image|screenshot)\b)"
        r"(?=.*\b(skatteverket|personnummer|samordningsnummer|folkbokföring|"
        r"deklaration|coordination number|personal identity number|"
        r"tax return)\b)",
    ],
    "legal": [
        r"(?=.*\b(document|letter|form|contract|agreement|notice|"
        r"picture|photo|image|screenshot)\b)"
        # Deliberately not `landlord` or `dispute` on their own. The second
        # term has to name a tenancy *document*, because "your landlord gives
        # you a lock cylinder, which you can see in the picture" is a correct
        # answer about a laundry room and was refused as a legal matter while
        # `landlord` was in this list.
        r"(?=.*\b(tenancy|lease|hyresavtal|andrahand|sublet|eviction|"
        r"rental (contract|agreement)|clause|terms and conditions)\b)",
    ],
    "medical_safety": [
        r"(?=.*\b(document|letter|form|picture|photo|image|screenshot|"
        r"prescription|test result|referral)\b)"
        r"(?=.*\b(diagnosis|prescription|medication|test result|referral|"
        r"vårdcentral|1177|hospital|journal)\b)",
    ],
}


def check_response_guardrails(text: str, *,
                              has_image: bool = False) -> Optional[Dict[str, Any]]:
    """Scan a model-generated answer for something it must not have said.

    Separate from `check_query_guardrails` on purpose. Running the query
    patterns over an answer refuses the product's own explanations — see the
    note above this function.

    `has_image` widens the scan to cover the model identifying the photograph
    as a document in a restricted domain, because in that case nothing has read
    the subject of the request at all.
    """
    if not text:
        return None

    text_lower = text.lower()

    sets = [RESPONSE_DETERMINATION_PATTERNS]
    if has_image:
        sets.append(IMAGE_DOCUMENT_PATTERNS)

    for patterns_by_category in sets:
        for category_id, patterns in patterns_by_category.items():
            for pattern in patterns:
                if not re.search(pattern, text_lower):
                    continue
                config = REFUSAL_CATEGORIES[category_id]
                return {
                    "refused": True,
                    "category": category_id,
                    "title": config["title"],
                    "authority": config["authority"],
                    "message": config["message"],
                    "routing": config["routing"]
                }

    return None


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
